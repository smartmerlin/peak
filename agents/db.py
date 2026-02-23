import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

from agents.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS window_spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration_ms INTEGER,
    app_name TEXT NOT NULL,
    window_title TEXT NOT NULL DEFAULT '',
    url TEXT,
    machine TEXT NOT NULL,
    idle_during BOOLEAN NOT NULL DEFAULT 0,
    active_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_spans_start ON window_spans(start_time);
CREATE INDEX IF NOT EXISTS idx_spans_machine ON window_spans(machine);
CREATE INDEX IF NOT EXISTS idx_spans_end ON window_spans(end_time);

CREATE TABLE IF NOT EXISTS enrichments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    data TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enrichments_ts ON enrichments(timestamp);
CREATE INDEX IF NOT EXISTS idx_enrichments_source ON enrichments(source);

CREATE TABLE IF NOT EXISTS classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    block_start TEXT NOT NULL,
    block_end TEXT NOT NULL,
    project TEXT NOT NULL,
    task TEXT,
    work_type TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'medium',
    classification_tier TEXT NOT NULL,
    verified BOOLEAN NOT NULL DEFAULT 0,
    active_minutes REAL
);

CREATE INDEX IF NOT EXISTS idx_class_block ON classifications(block_start);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classification_id INTEGER NOT NULL REFERENCES classifications(id),
    original_project TEXT,
    corrected_project TEXT,
    original_task TEXT,
    corrected_task TEXT,
    corrected_work_type TEXT,
    signals_snapshot TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_type TEXT NOT NULL,
    condition_value TEXT NOT NULL,
    project TEXT NOT NULL,
    task_template TEXT,
    work_type TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT 1
);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    conn.close()


@contextmanager
def db_session(db_path: Path | None = None):
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Span helpers ---

def open_span(conn: sqlite3.Connection, app_name: str, window_title: str,
              machine: str, url: str | None = None) -> int:
    ts = now_iso()
    cursor = conn.execute(
        """INSERT INTO window_spans (start_time, app_name, window_title, url, machine)
           VALUES (?, ?, ?, ?, ?)""",
        (ts, app_name, window_title, url, machine),
    )
    conn.commit()
    return cursor.lastrowid


def close_span(conn: sqlite3.Connection, span_id: int) -> None:
    ts = now_iso()
    row = conn.execute(
        "SELECT start_time, idle_during FROM window_spans WHERE id = ?", (span_id,)
    ).fetchone()
    if not row:
        return
    start = datetime.fromisoformat(row["start_time"])
    end = datetime.fromisoformat(ts)
    duration_ms = int((end - start).total_seconds() * 1000)
    active_ms = 0 if row["idle_during"] else duration_ms
    conn.execute(
        """UPDATE window_spans
           SET end_time = ?, duration_ms = ?, active_ms = ?
           WHERE id = ?""",
        (ts, duration_ms, active_ms, span_id),
    )
    conn.commit()


def close_current_span(conn: sqlite3.Connection, machine: str) -> int | None:
    """Close the most recent open span for a machine. Returns span_id or None."""
    row = conn.execute(
        """SELECT id FROM window_spans
           WHERE machine = ? AND end_time IS NULL
           ORDER BY start_time DESC LIMIT 1""",
        (machine,),
    ).fetchone()
    if row:
        close_span(conn, row["id"])
        return row["id"]
    return None


def mark_span_idle(conn: sqlite3.Connection, span_id: int, idle_ms: int) -> None:
    conn.execute(
        """UPDATE window_spans
           SET idle_during = 1, active_ms = MAX(0, COALESCE(duration_ms, 0) - ?)
           WHERE id = ?""",
        (idle_ms, span_id),
    )
    conn.commit()


# --- Enrichment helpers ---

def add_enrichment(conn: sqlite3.Connection, source: str, data: dict) -> int:
    ts = now_iso()
    cursor = conn.execute(
        "INSERT INTO enrichments (timestamp, source, data) VALUES (?, ?, ?)",
        (ts, source, json.dumps(data)),
    )
    conn.commit()
    return cursor.lastrowid


# --- Project helpers ---

def add_project(conn: sqlite3.Connection, name: str, description: str = "") -> int:
    cursor = conn.execute(
        "INSERT OR IGNORE INTO projects (name, description) VALUES (?, ?)",
        (name, description),
    )
    conn.commit()
    return cursor.lastrowid
