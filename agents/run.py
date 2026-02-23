"""
Main entry point — starts the appropriate agent + screenshot capture + Chrome event server.

Usage:
    python -m agents.run                # Auto-detect platform
    python -m agents.run --standalone   # Mac: write directly to SQLite
"""

import platform
import signal
import sys
import threading

from agents.config import load_config
from agents import db, server
from agents.screenshot import ScreenshotCapture


def main():
    config = load_config()
    standalone = "--standalone" in sys.argv
    system = platform.system()

    # Init DB
    db.init_db()
    print(f"[peak] Database initialized at {db.DB_PATH}")

    # Start screenshot capture in background
    screenshotter = ScreenshotCapture(config, standalone=True)
    screenshot_thread = threading.Thread(target=screenshotter.start, daemon=True)
    screenshot_thread.start()

    if system == "Darwin":
        from agents.mac.agent import MacAgent
        agent = MacAgent(config, standalone=standalone or True)
    elif system == "Windows":
        from agents.windows.agent import WindowsAgent
        agent = WindowsAgent(config)
    else:
        print(f"[peak] Unsupported platform: {system}")
        sys.exit(1)

    # Wire up Chrome event server
    server.set_agent(agent)
    server.start_server(config)

    def shutdown(sig, frame):
        agent.stop()
        screenshotter.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start agent (blocks)
    agent.start()


if __name__ == "__main__":
    main()
