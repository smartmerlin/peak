"""
Screenshot capture module.

Captures the active window every 30s, resizes to 720p, saves as JPEG.
Pixel-diff detects screen changes. Stores metadata as enrichments.

Usage:
    python -m agents.screenshot [--standalone]
"""

import hashlib
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from agents.config import load_config, SCREENSHOTS_DIR
from agents import db


def _get_frontmost_window_id() -> int | None:
    """Get the CGWindowID of the frontmost window using Quartz."""
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
        for win in windows:
            # Layer 0 = normal windows; skip menubar, dock, etc.
            if win.get("kCGWindowLayer", 999) == 0 and win.get("kCGWindowOwnerName"):
                return win.get("kCGWindowNumber")
    except Exception:
        pass
    return None


def _capture_active_window() -> Image.Image | None:
    """Capture the active window on macOS using screencapture."""
    wid = _get_frontmost_window_id()
    if wid is None:
        return None

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # -x = no sound, -l = window ID, -o = no shadow
        result = subprocess.run(
            ["screencapture", "-x", "-o", "-l", str(wid), tmp_path],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        img = Image.open(tmp_path).convert("RGB")
        return img
    except Exception:
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


class ScreenshotCapture:
    def __init__(self, config: dict, standalone: bool = False):
        self.config = config
        self.interval = config["screenshot_interval_s"]
        self.standalone = standalone
        self.last_hash: str | None = None
        self._stop = threading.Event()
        self.db_conn = None

    def start(self):
        if self.standalone:
            db.init_db()
            self.db_conn = db.get_connection()

        print(f"[peak] Screenshot capture started (interval={self.interval}s)")

        while not self._stop.is_set():
            try:
                self._capture()
            except Exception as e:
                print(f"[peak] Screenshot error: {e}")
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self.db_conn:
            self.db_conn.close()
        print("[peak] Screenshot capture stopped.")

    def _capture(self):
        img = _capture_active_window()
        if img is None:
            return

        # Resize to 720p height, maintaining aspect ratio
        target_h = 720
        ratio = target_h / img.height
        target_w = int(img.width * ratio)
        img = img.resize((target_w, target_h), Image.LANCZOS)

        # Pixel-diff: compute hash of downsampled image
        small = img.resize((160, 90), Image.NEAREST)
        arr = np.array(small)
        current_hash = hashlib.md5(arr.tobytes()).hexdigest()
        screen_changed = current_hash != self.last_hash
        self.last_hash = current_hash

        # Save
        ts = datetime.now(timezone.utc)
        filename = ts.strftime("%Y%m%d_%H%M%S") + ".jpg"
        date_dir = SCREENSHOTS_DIR / ts.strftime("%Y-%m-%d")
        date_dir.mkdir(exist_ok=True)
        filepath = date_dir / filename
        img.save(str(filepath), "JPEG", quality=75)

        # Store as enrichment
        enrichment_data = {
            "filepath": str(filepath),
            "screen_changed": screen_changed,
            "resolution": f"{target_w}x{target_h}",
        }

        if self.standalone and self.db_conn:
            db.add_enrichment(self.db_conn, "screenshot", enrichment_data)


def main():
    standalone = "--standalone" in sys.argv
    config = load_config()
    capture = ScreenshotCapture(config, standalone=standalone)

    def shutdown(sig, frame):
        capture.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    capture.start()


if __name__ == "__main__":
    main()
