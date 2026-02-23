"""
Spot-check tool for verifying classifications.

Usage:
    python -m agents.spot_check              # Show last 20 blocks
    python -m agents.spot_check --all        # Show all classifications
    python -m agents.spot_check --stats      # Show summary stats
    python -m agents.spot_check --block TIME # Show detail for one block
"""

import json
import sys
from datetime import datetime, timezone

from agents import db
from agents.classifier import gather_block_context, select_screenshots


def show_classifications(limit: int = 20):
    conn = db.get_connection()
    rows = db.get_recent_classifications(conn, limit=limit)
    conn.close()

    if not rows:
        print("No classifications yet.")
        return

    print(f"\n{'Block Start':<28} {'Project':<20} {'Work Type':<14} {'Conf':<8} {'Active':<8} {'Task'}")
    print("-" * 110)
    for r in reversed(rows):
        print(
            f"{r['block_start']:<28} "
            f"{r['project']:<20} "
            f"{r['work_type']:<14} "
            f"{r['confidence']:<8} "
            f"{r['active_minutes']:<8} "
            f"{r.get('task', '') or ''}"
        )
    print(f"\nShowing {len(rows)} classifications (most recent first in DB)")


def show_stats():
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT
            project,
            work_type,
            confidence,
            COUNT(*) as cnt,
            SUM(active_minutes) as total_active,
            SUM(CASE WHEN verified THEN 1 ELSE 0 END) as verified_cnt
           FROM classifications
           GROUP BY project, work_type
           ORDER BY total_active DESC"""
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) as c FROM classifications").fetchone()["c"]
    conn.close()

    if not rows:
        print("No classifications yet.")
        return

    print(f"\n{'Project':<20} {'Work Type':<14} {'Blocks':<8} {'Active Min':<12} {'Verified':<10} {'Avg Conf'}")
    print("-" * 80)
    for r in rows:
        print(
            f"{r['project']:<20} "
            f"{r['work_type']:<14} "
            f"{r['cnt']:<8} "
            f"{r['total_active']:<12.1f} "
            f"{r['verified_cnt']:<10} "
            f"{r['confidence']}"
        )
    print(f"\nTotal blocks classified: {total}")


def show_block_detail(block_start: str):
    conn = db.get_connection()

    # Get classification
    row = conn.execute(
        "SELECT * FROM classifications WHERE block_start = ?", (block_start,)
    ).fetchone()

    if not row:
        print(f"No classification for block starting at {block_start}")
        conn.close()
        return

    row = dict(row)
    block_end = row["block_end"]

    print(f"\n=== Block: {block_start} to {block_end} ===")
    print(f"  Project:    {row['project']}")
    print(f"  Task:       {row.get('task', '')}")
    print(f"  Work type:  {row['work_type']}")
    print(f"  Confidence: {row['confidence']}")
    print(f"  Tier:       {row['classification_tier']}")
    print(f"  Active min: {row['active_minutes']}")
    print(f"  Verified:   {bool(row['verified'])}")

    # Get raw context
    context = gather_block_context(conn, block_start, block_end)
    if not context.get("empty"):
        print(f"\n--- Raw Signals ---")
        print(f"  Spans: {context['span_count']} ({context['app_switches']} switches)")
        print(f"  Dominant app: {context['dominant_app']}")
        print(f"  Dominant title: {context['dominant_title']}")
        print(f"  Active: {context['active_minutes']}m, Idle: {context['idle_minutes']}m")
        print(f"  Apps: {', '.join(context['unique_apps'])}")
        print(f"\n  Window history:")
        for s in context["span_summaries"]:
            print(f"    - {s}")
        if context["urls"]:
            print(f"\n  URLs:")
            for u in context["urls"]:
                print(f"    - {u}")

    # Get screenshots
    screenshots = select_screenshots(conn, block_start, block_end)
    if screenshots:
        print(f"\n  Screenshots ({len(screenshots)}):")
        for ss in screenshots:
            changed = " [CHANGED]" if ss["screen_changed"] else ""
            print(f"    - {ss['filepath']}{changed}")

    conn.close()


def main():
    db.init_db()

    if "--stats" in sys.argv:
        show_stats()
    elif "--block" in sys.argv:
        idx = sys.argv.index("--block")
        if idx + 1 < len(sys.argv):
            show_block_detail(sys.argv[idx + 1])
        else:
            print("Usage: --block <block_start_time>")
    elif "--all" in sys.argv:
        show_classifications(limit=9999)
    else:
        show_classifications(limit=20)


if __name__ == "__main__":
    main()
