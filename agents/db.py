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


def get_active_projects(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, description FROM projects WHERE active = 1"
    ).fetchall()
    return [dict(r) for r in rows]


def seed_projects(conn: sqlite3.Connection, projects: list[dict]) -> None:
    """Upsert projects from config. Adds new ones, updates descriptions."""
    for p in projects:
        existing = conn.execute(
            "SELECT id FROM projects WHERE name = ?", (p["name"],)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE projects SET description = ? WHERE name = ?",
                (p.get("description", ""), p["name"]),
            )
        else:
            conn.execute(
                "INSERT INTO projects (name, description) VALUES (?, ?)",
                (p["name"], p.get("description", "")),
            )
    conn.commit()


# --- Span query helpers ---

def get_spans_in_range(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """Get all spans overlapping with [start, end)."""
    rows = conn.execute(
        """SELECT * FROM window_spans
           WHERE start_time < ? AND (end_time > ? OR end_time IS NULL)
           ORDER BY start_time""",
        (end, start),
    ).fetchall()
    return [dict(r) for r in rows]


def get_enrichments_in_range(
    conn: sqlite3.Connection, start: str, end: str, source: str | None = None
) -> list[dict]:
    """Get enrichments in [start, end), optionally filtered by source."""
    if source:
        rows = conn.execute(
            """SELECT * FROM enrichments
               WHERE timestamp >= ? AND timestamp < ? AND source = ?
               ORDER BY timestamp""",
            (start, end, source),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM enrichments
               WHERE timestamp >= ? AND timestamp < ?
               ORDER BY timestamp""",
            (start, end),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Classification helpers ---

def block_is_classified(conn: sqlite3.Connection, block_start: str) -> bool:
    row = conn.execute(
        "SELECT id FROM classifications WHERE block_start = ?", (block_start,)
    ).fetchone()
    return row is not None


def insert_classification(
    conn: sqlite3.Connection,
    block_start: str,
    block_end: str,
    project: str,
    task: str | None,
    work_type: str,
    confidence: str,
    classification_tier: str,
    active_minutes: float,
) -> int:
    cursor = conn.execute(
        """INSERT INTO classifications
           (block_start, block_end, project, task, work_type, confidence,
            classification_tier, active_minutes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (block_start, block_end, project, task, work_type, confidence,
         classification_tier, active_minutes),
    )
    conn.commit()
    return cursor.lastrowid


def get_recent_classifications(
    conn: sqlite3.Connection, limit: int = 50
) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*, p.name as project_name
           FROM classifications c
           LEFT JOIN projects p ON c.project = p.name
           ORDER BY c.block_start DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_day_classifications(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    """Get all classifications for a date (YYYY-MM-DD). Returns in time order."""
    start = f"{date_str}T00:00:00"
    end = f"{date_str}T23:59:59"
    rows = conn.execute(
        """SELECT * FROM classifications
           WHERE block_start >= ? AND block_start <= ?
           ORDER BY block_start""",
        (start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def get_classification_by_id(conn: sqlite3.Connection, cls_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM classifications WHERE id = ?", (cls_id,)
    ).fetchone()
    return dict(row) if row else None


def update_classification(
    conn: sqlite3.Connection,
    cls_id: int,
    project: str,
    task: str | None,
    work_type: str,
) -> None:
    """Update a classification after correction."""
    conn.execute(
        """UPDATE classifications
           SET project = ?, task = ?, work_type = ?, verified = 1
           WHERE id = ?""",
        (project, task, work_type, cls_id),
    )
    conn.commit()


def insert_correction(
    conn: sqlite3.Connection,
    classification_id: int,
    original_project: str,
    corrected_project: str,
    original_task: str | None,
    corrected_task: str | None,
    corrected_work_type: str,
    signals_snapshot: dict | None = None,
) -> int:
    cursor = conn.execute(
        """INSERT INTO corrections
           (classification_id, original_project, corrected_project,
            original_task, corrected_task, corrected_work_type,
            signals_snapshot, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (classification_id, original_project, corrected_project,
         original_task, corrected_task, corrected_work_type,
         json.dumps(signals_snapshot) if signals_snapshot else None,
         now_iso()),
    )
    conn.commit()
    return cursor.lastrowid


def bulk_verify(conn: sqlite3.Connection, date_str: str) -> int:
    """Mark all high-confidence classifications for a date as verified. Returns count."""
    start = f"{date_str}T00:00:00"
    end = f"{date_str}T23:59:59"
    cursor = conn.execute(
        """UPDATE classifications
           SET verified = 1
           WHERE block_start >= ? AND block_start <= ?
             AND confidence = 'high' AND verified = 0""",
        (start, end),
    )
    conn.commit()
    return cursor.rowcount


def get_day_stats(conn: sqlite3.Connection, date_str: str) -> dict:
    """Compute aggregate stats for a day."""
    start = f"{date_str}T00:00:00"
    end = f"{date_str}T23:59:59"
    rows = conn.execute(
        """SELECT work_type, COUNT(*) as blocks,
                  SUM(active_minutes) as total_active
           FROM classifications
           WHERE block_start >= ? AND block_start <= ?
           GROUP BY work_type""",
        (start, end),
    ).fetchall()

    stats = {
        "total_blocks": 0,
        "total_active_minutes": 0.0,
        "deep_work_minutes": 0.0,
        "shallow_work_minutes": 0.0,
        "meeting_minutes": 0.0,
        "break_minutes": 0.0,
        "personal_minutes": 0.0,
    }
    for r in rows:
        active = r["total_active"] or 0
        stats["total_blocks"] += r["blocks"]
        stats["total_active_minutes"] += active
        stats[f"{r['work_type']}_minutes"] = round(active, 1)

    stats["total_active_minutes"] = round(stats["total_active_minutes"], 1)

    # Verified / unverified counts
    counts = conn.execute(
        """SELECT
            SUM(CASE WHEN verified THEN 1 ELSE 0 END) as verified,
            SUM(CASE WHEN NOT verified THEN 1 ELSE 0 END) as unverified
           FROM classifications
           WHERE block_start >= ? AND block_start <= ?""",
        (start, end),
    ).fetchone()
    stats["verified_blocks"] = counts["verified"] or 0
    stats["unverified_blocks"] = counts["unverified"] or 0

    return stats
