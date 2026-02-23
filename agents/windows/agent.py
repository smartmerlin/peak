"""
Windows agent: focus tracking + hybrid idle detection + Mac JSONL ingestion + Parsec.

Tracks active window on Windows. When Parsec is focused, defers to the
Mac agents. Ingests Mac JSONL files into window_spans.

Usage:
    python -m agents.windows_agent
"""

import atexit
import ctypes
import ctypes.wintypes
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from agents.config import load_config, JSONL_DIR
from agents import db

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 constants
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
WM_WTSSESSION_CHANGE = 0x02B1

# Parsec window title patterns
PARSEC_PATTERNS = ["Parsec", "parsec"]

# Map Parsec window titles to Mac machine IDs
PARSEC_MACHINE_MAP = {
    "mac-1": ["mac-1", "Mac-1", "Mac Mini 1"],
    "mac-2": ["mac-2", "Mac-2", "Mac Mini 2"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_foreground_window_info() -> tuple[str, str]:
    """Returns (app_name, window_title) for the current foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ("Unknown", "")

    # Get window title
    length = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    window_title = buf.value

    # Get PID -> process name
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        proc = psutil.Process(pid.value)
        app_name = proc.name().replace(".exe", "")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        app_name = "Unknown"

    return (app_name, window_title)


def get_last_input_seconds() -> float:
    """Seconds since last keyboard/mouse input."""

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if user32.GetLastInputInfo(ctypes.byref(lii)):
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return millis / 1000.0
    return 0.0


def is_parsec_window(app_name: str, window_title: str) -> bool:
    return any(p in app_name for p in PARSEC_PATTERNS)


def parsec_to_machine(window_title: str) -> str | None:
    """Map Parsec window title to mac-1 or mac-2."""
    title_lower = window_title.lower()
    for machine_id, patterns in PARSEC_MACHINE_MAP.items():
        for pattern in patterns:
            if pattern.lower() in title_lower:
                return machine_id
    return None


class WindowsAgent:
    def __init__(self, config: dict):
        self.config = config
        self.machine_id = "windows"
        self.idle_threshold_s = config["idle_threshold_s"]
        self.idle_poll_interval_s = config["idle_poll_interval_s"]

        # DB
        db.init_db()
        self.db_conn = db.get_connection()

        # Current state
        self.current_span_id: int | None = None
        self.current_app: str | None = None
        self.is_idle = False
        self.idle_start: float | None = None
        self.in_parsec = False
        self.parsec_machine: str | None = None
        self.lock = threading.Lock()
        self._stop = threading.Event()

        # JSONL ingestion: track byte offsets per file
        self.jsonl_offsets: dict[str, int] = {}

    def start(self):
        # Close any orphaned spans from a previous crash/forced kill
        self._close_orphaned_spans()

        print(f"[peak] Windows agent started")
        atexit.register(self._cleanup)

        # Start idle polling thread
        idle_thread = threading.Thread(target=self._idle_poll_loop, daemon=True)
        idle_thread.start()

        # Start JSONL ingestion thread
        jsonl_thread = threading.Thread(target=self._jsonl_ingest_loop, daemon=True)
        jsonl_thread.start()

        # Set up foreground window hook via Win32
        # WinEventProc callback
        WINEVENTPROC = ctypes.WINFUNCTYPE(
            None,
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.HWND,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD,
        )

        def callback(hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
            self._on_foreground_change()

        self._callback = WINEVENTPROC(callback)
        user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
            0, self._callback, 0, 0, WINEVENT_OUTOFCONTEXT,
        )

        # Record initial state
        self._on_foreground_change()

        # Message loop
        msg = ctypes.wintypes.MSG()
        try:
            while not self._stop.is_set():
                if user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
                    if msg.message == WM_WTSSESSION_CHANGE:
                        if msg.wParam == WTS_SESSION_LOCK:
                            self._on_session_lock()
                        elif msg.wParam == WTS_SESSION_UNLOCK:
                            self._on_session_unlock()
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        self._stop.set()
        self._cleanup()

    def _cleanup(self):
        """Close open spans and DB connection. Safe to call multiple times."""
        with self.lock:
            if self.current_span_id:
                try:
                    db.close_span(self.db_conn, self.current_span_id)
                except Exception:
                    pass
                self.current_span_id = None
        try:
            self.db_conn.close()
        except Exception:
            pass
        print("[peak] Windows agent stopped.")

    def _close_orphaned_spans(self):
        """Close any spans left open from a previous crash."""
        rows = self.db_conn.execute(
            "SELECT id FROM window_spans WHERE machine = ? AND end_time IS NULL",
            (self.machine_id,),
        ).fetchall()
        for row in rows:
            db.close_span(self.db_conn, row["id"])
        if rows:
            print(f"[peak] Closed {len(rows)} orphaned span(s) from previous run")

    def _on_foreground_change(self):
        app_name, window_title = get_foreground_window_info()

        with self.lock:
            # Close previous span
            if self.current_span_id:
                db.close_span(self.db_conn, self.current_span_id)
                self.current_span_id = None

            # Check if Parsec
            if is_parsec_window(app_name, window_title):
                self.in_parsec = True
                self.parsec_machine = parsec_to_machine(window_title)
                # Don't create a span — the Mac agent handles its own timeline
                return
            else:
                self.in_parsec = False
                self.parsec_machine = None

            # Open new span
            self.current_span_id = db.open_span(
                self.db_conn, app_name, window_title, self.machine_id
            )
            self.current_app = app_name

    def _on_session_lock(self):
        with self.lock:
            self.is_idle = True
            self.idle_start = time.time()

    def _on_session_unlock(self):
        with self.lock:
            if self.is_idle and self.idle_start and self.current_span_id:
                idle_ms = int((time.time() - self.idle_start) * 1000)
                db.mark_span_idle(self.db_conn, self.current_span_id, idle_ms)
            self.is_idle = False
            self.idle_start = None

    def _idle_poll_loop(self):
        """Poll GetLastInputInfo for soft idle."""
        while not self._stop.is_set():
            time.sleep(self.idle_poll_interval_s)
            idle_seconds = get_last_input_seconds()

            with self.lock:
                if idle_seconds >= self.idle_threshold_s and not self.is_idle:
                    self.is_idle = True
                    self.idle_start = time.time() - idle_seconds
                elif idle_seconds < self.idle_threshold_s and self.is_idle:
                    if self.idle_start and self.current_span_id:
                        idle_ms = int((time.time() - self.idle_start) * 1000)
                        db.mark_span_idle(self.db_conn, self.current_span_id, idle_ms)
                    self.is_idle = False
                    self.idle_start = None

    def _jsonl_ingest_loop(self):
        """Tail Mac agent JSONL files and ingest into window_spans."""
        jsonl_dir = Path(self.config["jsonl_sync_dir"])
        while not self._stop.is_set():
            time.sleep(2)
            if not jsonl_dir.exists():
                continue

            for jsonl_file in jsonl_dir.glob("*.jsonl"):
                self._ingest_file(jsonl_file)

    def _ingest_file(self, path: Path):
        key = str(path)
        offset = self.jsonl_offsets.get(key, 0)

        try:
            size = path.stat().st_size
            if size <= offset:
                return

            with open(path, "r") as f:
                f.seek(offset)
                new_lines = f.read()
                new_offset = f.tell()

            for line in new_lines.strip().split("\n"):
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    self._insert_span_from_jsonl(record)
                except json.JSONDecodeError:
                    continue

            self.jsonl_offsets[key] = new_offset
        except Exception as e:
            print(f"[peak] JSONL ingest error ({path.name}): {e}")

    def _insert_span_from_jsonl(self, record: dict):
        self.db_conn.execute(
            """INSERT INTO window_spans
               (start_time, end_time, duration_ms, app_name, window_title,
                url, machine, idle_during, active_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["start_time"],
                record["end_time"],
                record["duration_ms"],
                record["app_name"],
                record["window_title"],
                record.get("url"),
                record["machine"],
                record.get("idle_during", False),
                record.get("active_ms", record["duration_ms"]),
            ),
        )
        self.db_conn.commit()

    def handle_chrome_event(self, tab_title: str, url: str):
        """Called by the Chrome event HTTP server."""
        with self.lock:
            if self.current_span_id:
                db.close_span(self.db_conn, self.current_span_id)
            self.current_span_id = db.open_span(
                self.db_conn, "Google Chrome", tab_title, self.machine_id, url
            )


def main():
    config = load_config()
    agent = WindowsAgent(config)

    def shutdown(sig, frame):
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    agent.start()


if __name__ == "__main__":
    main()
