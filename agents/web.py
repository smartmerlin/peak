"""
Web UI for viewing timeline and correcting classifications.

Runs on localhost:7835 (configurable).
"""

import json
import threading
from pathlib import Path

from flask import Flask, request, jsonify, send_file, send_from_directory

from agents import db
from agents.config import SCREENSHOTS_DIR
from agents.classifier import gather_block_context, select_screenshots

_template_dir = Path(__file__).parent / "templates"
_static_dir = Path(__file__).parent / "static"

app = Flask(__name__, template_folder=str(_template_dir), static_folder=str(_static_dir))


@app.route("/")
def index():
    return send_file(str(_template_dir / "timeline.html"))


# --- API routes ---

@app.route("/api/timeline")
def api_timeline():
    date_str = request.args.get("date")
    if not date_str:
        from datetime import datetime, timezone
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = db.get_connection()
    try:
        classifications = db.get_day_classifications(conn, date_str)
        return jsonify({"date": date_str, "blocks": classifications})
    finally:
        conn.close()


@app.route("/api/block/<int:cls_id>")
def api_block_detail(cls_id):
    conn = db.get_connection()
    try:
        cls = db.get_classification_by_id(conn, cls_id)
        if not cls:
            return jsonify({"error": "not found"}), 404

        context = gather_block_context(conn, cls["block_start"], cls["block_end"])
        screenshots = select_screenshots(conn, cls["block_start"], cls["block_end"])

        # Convert screenshot filepaths to serveable URLs
        for ss in screenshots:
            fp = ss["filepath"]
            # Make relative to screenshots dir for URL
            try:
                rel = Path(fp).relative_to(SCREENSHOTS_DIR)
                ss["url"] = f"/screenshots/{rel}"
            except ValueError:
                ss["url"] = None

        return jsonify({
            "classification": cls,
            "context": context,
            "screenshots": screenshots,
        })
    finally:
        conn.close()


@app.route("/api/projects")
def api_projects():
    conn = db.get_connection()
    try:
        projects = db.get_active_projects(conn)
        return jsonify({"projects": projects})
    finally:
        conn.close()


@app.route("/api/stats")
def api_stats():
    date_str = request.args.get("date")
    if not date_str:
        from datetime import datetime, timezone
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = db.get_connection()
    try:
        stats = db.get_day_stats(conn, date_str)
        return jsonify({"date": date_str, **stats})
    finally:
        conn.close()


@app.route("/api/correct", methods=["POST"])
def api_correct():
    data = request.get_json()
    if not data or "classification_id" not in data:
        return jsonify({"error": "missing classification_id"}), 400

    cls_id = data["classification_id"]
    conn = db.get_connection()
    try:
        cls = db.get_classification_by_id(conn, cls_id)
        if not cls:
            return jsonify({"error": "not found"}), 404

        new_project = data.get("project", cls["project"])
        new_task = data.get("task", cls["task"])
        new_work_type = data.get("work_type", cls["work_type"])

        # Capture signals for few-shot learning
        context = gather_block_context(conn, cls["block_start"], cls["block_end"])

        # Record the correction
        db.insert_correction(
            conn,
            classification_id=cls_id,
            original_project=cls["project"],
            corrected_project=new_project,
            original_task=cls["task"],
            corrected_task=new_task,
            corrected_work_type=new_work_type,
            signals_snapshot=context if not context.get("empty") else None,
        )

        # Update the classification
        db.update_classification(conn, cls_id, new_project, new_task, new_work_type)

        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/bulk-verify", methods=["POST"])
def api_bulk_verify():
    data = request.get_json() or {}
    date_str = data.get("date")
    if not date_str:
        from datetime import datetime, timezone
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = db.get_connection()
    try:
        count = db.bulk_verify(conn, date_str)
        return jsonify({"ok": True, "verified_count": count})
    finally:
        conn.close()


@app.route("/screenshots/<path:filepath>")
def serve_screenshot(filepath):
    return send_from_directory(str(SCREENSHOTS_DIR), filepath)


def start_web_ui(config: dict):
    """Start the web UI server in a background thread."""
    port = config.get("web_ui_port", 7835)
    thread = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=port, debug=False, use_reloader=False
        ),
        daemon=True,
    )
    thread.start()
    print(f"[peak] Web UI running at http://127.0.0.1:{port}")
    return thread
