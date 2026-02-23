"""
Mac agent: focus tracking + hybrid idle detection.

Tracks active window/app, writes spans to JSONL file for ingestion by
the Windows agent (or directly to SQLite if running standalone).

Usage:
    python -m agents.mac_agent [--standalone]

With --standalone, writes directly to SQLite instead of JSONL.
"""

import json
import objc
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from AppKit import (
    NSApplication,
    NSObject,
    NSRunLoop,
    NSDate,
    NSWorkspace,
    NSWorkspaceDidActivateApplicationNotification,
)
from Quartz import (
    CGEventSourceSecondsSinceLastEventType,
    kCGEventSourceStateCombinedSessionState,
    kCGAnyInputEventType,
)
from Foundation import NSDistributedNotificationCenter

from agents.config import load_config
from agents import db


# AppleScript to get frontmost app name + window title.
# Uses the app's own scripting interface for title when possible (doesn't
# need accessibility), then falls back to System Events (needs accessibility).
WINDOW_INFO_SCRIPT = '''
tell application "System Events"
    set frontProc to first application process whose frontmost is true
    set appName to name of frontProc
end tell

set winTitle to ""
try
    tell application "System Events"
        tell process appName
            set winTitle to name of window 1
        end tell
    end tell
end try

return appName & "|||" & winTitle
'''

# Browser-specific scripts — these use the app's own scripting dictionary,
# so they work without accessibility permissions.
CHROME_INFO_SCRIPT = '''
tell application "{app}"
    try
        set tabTitle to title of active tab of front window
    on error
        set tabTitle to ""
    end try
    try
        set tabURL to URL of active tab of front window
    on error
        set tabURL to ""
    end try
    return tabTitle & "|||" & tabURL
end tell
'''

SAFARI_INFO_SCRIPT = '''
tell application "Safari"
    try
        set tabTitle to name of current tab of front window
    on error
        set tabTitle to ""
    end try
    try
        set tabURL to URL of current tab of front window
    on error
        set tabURL to ""
    end try
    return tabTitle & "|||" & tabURL
end tell
'''

CHROME_APPS = {"Google Chrome", "Google Chrome Canary", "Arc",
               "Brave Browser", "Microsoft Edge", "Chromium"}

_accessibility_warned = False


def get_window_info() -> tuple[str, str]:
    """Returns (app_name, window_title). Window title may be empty
    if accessibility permissions are not granted."""
    global _accessibility_warned
    try:
        result = subprocess.run(
            ["osascript", "-e", WINDOW_INFO_SCRIPT],
            capture_output=True, text=True, timeout=2,
        )
        parts = result.stdout.strip().split("|||", 1)
        app_name = parts[0] if parts else "Unknown"
        window_title = parts[1] if len(parts) > 1 else ""
        if not window_title and not _accessibility_warned:
            _accessibility_warned = True
            print("[peak] Window titles empty — grant Accessibility access to "
                  "Terminal/iTerm in System Settings > Privacy & Security > Accessibility")
        return (app_name, window_title)
    except Exception:
        return ("Unknown", "")


def get_browser_info(app_name: str) -> tuple[str, str | None]:
    """Returns (tab_title, url) for browser apps. Works without accessibility."""
    if app_name in CHROME_APPS:
        script = CHROME_INFO_SCRIPT.format(app=app_name)
    elif app_name == "Safari":
        script = SAFARI_INFO_SCRIPT
    else:
        return ("", None)
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=2,
        )
        parts = result.stdout.strip().split("|||", 1)
        title = parts[0] if parts else ""
        url = parts[1] if len(parts) > 1 and parts[1] else None
        return (title, url)
    except Exception:
        return ("", None)


BROWSER_APPS = CHROME_APPS | {"Safari", "Firefox"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- NSObject subclass to receive notifications ---

class _Observer(NSObject):
    """Bridges macOS notifications to the MacAgent."""

    def initWithAgent_(self, agent):
        self = objc.super(_Observer, self).init()
        if self is None:
            return None
        self._agent = agent
        return self

    def onAppSwitch_(self, notification):
        app_info = notification.userInfo()
        app_name = app_info.get("NSApplicationName", "Unknown")
        self._agent._handle_focus_change(app_name)

    def onScreenLock_(self, notification):
        self._agent._on_screen_lock()

    def onScreenUnlock_(self, notification):
        self._agent._on_screen_unlock()


class MacAgent:
    def __init__(self, config: dict, standalone: bool = False):
        self.config = config
        self.machine_id = config["machine_id"]
        self.standalone = standalone
        self.idle_threshold_s = config["idle_threshold_s"]
        self.idle_poll_interval_s = config["idle_poll_interval_s"]

        # Current span state
        self.current_span: dict | None = None
        self.lock = threading.Lock()
        self.is_idle = False
        self.idle_start: float | None = None

        # JSONL output
        self.jsonl_path = Path(config["jsonl_sync_dir"]) / f"{self.machine_id}.jsonl"

        # DB connection for standalone mode
        self.db_conn = None
        self.current_span_id: int | None = None

        self._stop = threading.Event()
        self._observer = None

    def start(self):
        if self.standalone:
            db.init_db()
            self.db_conn = db.get_connection()

        # Start idle polling thread
        idle_thread = threading.Thread(target=self._idle_poll_loop, daemon=True)
        idle_thread.start()

        # Create the ObjC observer
        self._observer = _Observer.alloc().initWithAgent_(self)

        # Set up NSWorkspace notifications for focus changes
        workspace = NSWorkspace.sharedWorkspace()
        nc = workspace.notificationCenter()
        nc.addObserver_selector_name_object_(
            self._observer, b"onAppSwitch:",
            NSWorkspaceDidActivateApplicationNotification, None,
        )

        # Screen lock/unlock for idle detection
        dnc = NSDistributedNotificationCenter.defaultCenter()
        dnc.addObserver_selector_name_object_(
            self._observer, b"onScreenLock:",
            "com.apple.screenIsLocked", None,
        )
        dnc.addObserver_selector_name_object_(
            self._observer, b"onScreenUnlock:",
            "com.apple.screenIsUnlocked", None,
        )

        # Record initial state
        self._record_current_app()

        print(f"[peak] Mac agent started (machine={self.machine_id}, "
              f"standalone={self.standalone})")

        # Run the event loop
        NSApplication.sharedApplication()
        try:
            while not self._stop.is_set():
                NSRunLoop.currentRunLoop().runMode_beforeDate_(
                    "kCFRunLoopDefaultMode", NSDate.dateWithTimeIntervalSinceNow_(0.5)
                )
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        self._stop.set()
        with self.lock:
            self._close_current_span()
        if self.db_conn:
            self.db_conn.close()
        print("[peak] Mac agent stopped.")

    def _record_current_app(self):
        """Record whatever app is currently focused."""
        workspace = NSWorkspace.sharedWorkspace()
        active_app = workspace.activeApplication()
        if active_app:
            app_name = active_app.get("NSApplicationName", "Unknown")
            self._handle_focus_change(app_name)

    def _on_screen_lock(self):
        with self.lock:
            self.is_idle = True
            self.idle_start = time.time()
            if self.current_span:
                self.current_span["idle_during"] = True

    def _on_screen_unlock(self):
        with self.lock:
            if self.is_idle and self.idle_start:
                idle_ms = int((time.time() - self.idle_start) * 1000)
                if self.current_span:
                    self.current_span["idle_ms"] = self.current_span.get("idle_ms", 0) + idle_ms
                    if self.standalone and self.current_span_id:
                        db.mark_span_idle(self.db_conn, self.current_span_id, idle_ms)
            self.is_idle = False
            self.idle_start = None

    def _idle_poll_loop(self):
        """Poll for soft idle (no input events)."""
        while not self._stop.is_set():
            time.sleep(self.idle_poll_interval_s)
            idle_seconds = CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateCombinedSessionState, kCGAnyInputEventType
            )
            with self.lock:
                if idle_seconds >= self.idle_threshold_s and not self.is_idle:
                    self.is_idle = True
                    self.idle_start = time.time() - idle_seconds
                    if self.current_span:
                        self.current_span["idle_during"] = True
                elif idle_seconds < self.idle_threshold_s and self.is_idle:
                    if self.idle_start:
                        idle_ms = int((time.time() - self.idle_start) * 1000)
                        if self.current_span:
                            self.current_span["idle_ms"] = self.current_span.get("idle_ms", 0) + idle_ms
                            if self.standalone and self.current_span_id:
                                db.mark_span_idle(self.db_conn, self.current_span_id, idle_ms)
                    self.is_idle = False
                    self.idle_start = None

    def _handle_focus_change(self, app_name: str):
        if app_name in BROWSER_APPS:
            tab_title, url = get_browser_info(app_name)
            window_title = tab_title or app_name
        else:
            _, window_title = get_window_info()
            url = None

        with self.lock:
            self._close_current_span()
            self._open_new_span(app_name, window_title, url)

    def _open_new_span(self, app_name: str, window_title: str, url: str | None):
        ts = now_iso()
        self.current_span = {
            "start_time": ts,
            "app_name": app_name,
            "window_title": window_title,
            "url": url,
            "machine": self.machine_id,
            "idle_during": False,
            "idle_ms": 0,
        }
        if self.standalone:
            self.current_span_id = db.open_span(
                self.db_conn, app_name, window_title, self.machine_id, url
            )

    def _close_current_span(self):
        if not self.current_span:
            return

        ts = now_iso()
        span = self.current_span
        start = datetime.fromisoformat(span["start_time"])
        end = datetime.fromisoformat(ts)
        duration_ms = int((end - start).total_seconds() * 1000)
        active_ms = max(0, duration_ms - span.get("idle_ms", 0))

        if self.standalone:
            if self.current_span_id:
                db.close_span(self.db_conn, self.current_span_id)
        else:
            record = {
                "start_time": span["start_time"],
                "end_time": ts,
                "duration_ms": duration_ms,
                "app_name": span["app_name"],
                "window_title": span["window_title"],
                "url": span["url"],
                "machine": self.machine_id,
                "idle_during": span["idle_during"],
                "active_ms": active_ms,
            }
            self._write_jsonl(record)

        self.current_span = None
        self.current_span_id = None

    def _write_jsonl(self, record: dict):
        with open(self.jsonl_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def handle_chrome_event(self, tab_title: str, url: str):
        """Called by the Chrome event HTTP server."""
        with self.lock:
            self._close_current_span()
            self._open_new_span("Google Chrome", tab_title, url)


def main():
    standalone = "--standalone" in sys.argv
    config = load_config()
    agent = MacAgent(config, standalone=standalone)

    def shutdown(sig, frame):
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    agent.start()


if __name__ == "__main__":
    main()
