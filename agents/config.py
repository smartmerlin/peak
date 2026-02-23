import os
import json
from pathlib import Path

# Directories
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file if it exists
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())
DATA_DIR = BASE_DIR / "data"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
JSONL_DIR = DATA_DIR / "jsonl"
DB_PATH = DATA_DIR / "peak.db"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)
JSONL_DIR.mkdir(exist_ok=True)

# Agent config
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "machine_id": "mac-1",  # "windows", "mac-1", or "mac-2"
    "screenshot_interval_s": 30,
    "idle_poll_interval_s": 5,
    "idle_threshold_s": 120,
    "chrome_event_port": 7834,
    "web_ui_port": 7835,
    "jsonl_sync_dir": str(JSONL_DIR),
    "chrome_url_blocklist": [],
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            user_config = json.load(f)
        merged = {**DEFAULT_CONFIG, **user_config}
    else:
        merged = DEFAULT_CONFIG.copy()
        with open(CONFIG_PATH, "w") as f:
            json.dump(merged, f, indent=2)
    return merged
