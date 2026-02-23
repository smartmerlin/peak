"""
Local HTTP server for Chrome extension events (port 7834).

Receives tab switch events from the Chrome extension and forwards them
to the active agent (Mac or Windows) to create new spans.
"""

import threading
from flask import Flask, request, jsonify

from agents.config import load_config

app = Flask(__name__)

# Will be set by the agent that starts this server
_agent = None


def set_agent(agent):
    global _agent
    _agent = agent


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return response


@app.route("/chrome-event", methods=["POST", "OPTIONS"])
def chrome_event():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid json"}), 400

    tab_title = data.get("tab_title", "")
    url = data.get("url", "")

    if _agent:
        _agent.handle_chrome_event(tab_title, url)

    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "agent": _agent is not None})


def start_server(config: dict):
    """Start the Flask server in a background thread."""
    port = config["chrome_event_port"]
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    print(f"[peak] Chrome event server listening on http://127.0.0.1:{port}")
    return thread
