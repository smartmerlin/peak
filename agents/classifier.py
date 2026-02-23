"""
Classification pipeline — Tier 3 (LLM only) for Phase 2.

Runs every 5 minutes. For each unclassified 5-minute block:
1. Gathers context from window_spans
2. Selects 2-3 screenshots
3. Sends to Gemini 3 Flash via OpenRouter for classification
4. Writes result to classifications table
"""

import base64
import json
import os
import threading
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from agents import db


BLOCK_MINUTES = 5
VALID_WORK_TYPES = {"deep_work", "shallow_work", "meeting", "break", "personal"}
VALID_CONFIDENCE = {"high", "medium", "low"}

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _align_to_block(dt: datetime) -> datetime:
    """Floor a datetime to the nearest 5-minute boundary."""
    return dt.replace(minute=(dt.minute // BLOCK_MINUTES) * BLOCK_MINUTES,
                      second=0, microsecond=0)


def _block_range(block_start: datetime) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a 5-minute block."""
    end = block_start + timedelta(minutes=BLOCK_MINUTES)
    return block_start.isoformat(), end.isoformat()


def _get_pending_blocks(conn) -> list[datetime]:
    """Find 5-minute blocks that have spans but no classification yet.

    Looks at the last 2 hours of data (to avoid classifying ancient gaps)
    and skips the current in-progress block.
    """
    now = datetime.now(timezone.utc)
    current_block = _align_to_block(now)
    lookback = now - timedelta(hours=2)
    start_block = _align_to_block(lookback)

    pending = []
    cursor = start_block
    while cursor < current_block:
        start_iso, end_iso = _block_range(cursor)
        # Check if block has any spans
        spans = db.get_spans_in_range(conn, start_iso, end_iso)
        if spans and not db.block_is_classified(conn, start_iso):
            pending.append(cursor)
        cursor += timedelta(minutes=BLOCK_MINUTES)

    return pending


def gather_block_context(conn, block_start: str, block_end: str) -> dict:
    """Gather all signals for a 5-minute block.

    Filters out idle-only machines: if a machine has zero active_ms in the
    block but another machine does have activity, drop the idle machine's
    spans entirely (they're just noise from an unattended Mac while you
    work on Windows, etc.).
    """
    all_spans = db.get_spans_in_range(conn, block_start, block_end)

    if not all_spans:
        return {"empty": True}

    # Compute total active_ms across ALL spans first (for idle shortcut)
    total_active_ms_all = sum(s.get("active_ms") or 0 for s in all_spans)

    # Figure out which machines have active time
    machine_active = {}
    for s in all_spans:
        m = s.get("machine", "unknown")
        machine_active[m] = machine_active.get(m, 0) + (s.get("active_ms") or 0)

    active_machines = {m for m, ms in machine_active.items() if ms > 0}

    # If at least one machine is active, drop spans from idle-only machines
    if active_machines:
        spans = [s for s in all_spans if s.get("machine", "unknown") in active_machines]
    else:
        spans = all_spans

    # Compute stats on filtered spans
    app_counter = Counter()
    title_counter = Counter()
    urls = []
    total_active_ms = 0
    total_idle_ms = 0

    for s in spans:
        app_counter[s["app_name"]] += 1
        title_counter[s["window_title"]] += 1
        if s["url"]:
            urls.append(s["url"])
        active = s.get("active_ms") or 0
        duration = s.get("duration_ms") or 0
        total_active_ms += active
        total_idle_ms += max(0, duration - active)

    dominant_app = app_counter.most_common(1)[0][0] if app_counter else "Unknown"
    dominant_title = title_counter.most_common(1)[0][0] if title_counter else ""

    # Build span summaries (deduplicated)
    span_summaries = []
    seen = set()
    for s in spans:
        key = (s["app_name"], s["window_title"], s.get("url", ""))
        if key not in seen:
            seen.add(key)
            entry = f"{s['app_name']}: {s['window_title']}"
            if s.get("url"):
                entry += f" ({s['url']})"
            span_summaries.append(entry)

    active_minutes = total_active_ms / 60000
    idle_minutes = total_idle_ms / 60000

    return {
        "empty": False,
        "all_idle": total_active_ms_all == 0,
        "block_start": block_start,
        "block_end": block_end,
        "span_count": len(spans),
        "app_switches": max(0, len(spans) - 1),
        "dominant_app": dominant_app,
        "dominant_title": dominant_title,
        "active_minutes": round(active_minutes, 2),
        "idle_minutes": round(idle_minutes, 2),
        "unique_apps": list(app_counter.keys()),
        "span_summaries": span_summaries[:15],  # Cap at 15
        "urls": urls[:10],
    }


def select_screenshots(conn, block_start: str, block_end: str, max_count: int = 3) -> list[dict]:
    """Select up to max_count screenshots for a block, preferring screen_changed ones."""
    enrichments = db.get_enrichments_in_range(conn, block_start, block_end, source="screenshot")

    screenshots = []
    for e in enrichments:
        data = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
        filepath = data.get("filepath", "")
        if Path(filepath).exists():
            screenshots.append({
                "filepath": filepath,
                "timestamp": e["timestamp"],
                "screen_changed": data.get("screen_changed", False),
            })

    # Sort: screen_changed first, then by timestamp
    screenshots.sort(key=lambda x: (not x["screen_changed"], x["timestamp"]))

    # If we have more than max_count, space them out
    if len(screenshots) <= max_count:
        return screenshots

    # Take first (changed), last, and one from the middle
    selected = [screenshots[0]]
    if len(screenshots) > 2:
        mid = len(screenshots) // 2
        selected.append(screenshots[mid])
    selected.append(screenshots[-1])
    return selected[:max_count]


def _encode_screenshot(filepath: str) -> str | None:
    """Read and base64-encode a screenshot file."""
    try:
        with open(filepath, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("ascii")
    except Exception:
        return None


def _build_prompt(context: dict, projects: list[dict]) -> str:
    """Build the classification prompt text."""
    project_names = [p['name'] for p in projects]
    project_list = "\n".join(
        f"- \"{p['name']}\" — {p['description']}" for p in projects
    )

    spans_text = "\n".join(f"  - {s}" for s in context["span_summaries"])

    return f"""Classify this 5-minute block of computer activity into one of the projects listed below.

## Projects
{project_list}

## Block: {context['block_start']} to {context['block_end']}

### Activity Summary
- Active time: {context['active_minutes']} minutes
- Idle time: {context['idle_minutes']} minutes
- App switches: {context['app_switches']}
- Dominant app: {context['dominant_app']}
- Dominant window title: {context['dominant_title']}

### Window Focus History (deduplicated)
{spans_text}

### URLs visited
{chr(10).join(f'  - {u}' for u in context['urls']) if context['urls'] else '  (none)'}

## Instructions
Based on the activity above (and any attached screenshots), classify this block.

The valid project names are EXACTLY: {', '.join(f'"{n}"' for n in project_names)}

Respond with ONLY a JSON object, no other text:
{{
  "project": "<EXACT project name from the list — use only the name, not the description>",
  "task": "<brief description of what the user was doing, 5-15 words>",
  "work_type": "<one of: deep_work, shallow_work, meeting, break, personal>",
  "confidence": "<one of: high, medium, low>"
}}

Guidelines:
- "deep_work" = focused coding, writing, design, or analysis in one project
- "shallow_work" = email, Slack, quick replies, admin tasks, context switching
- "meeting" = video calls, calendar events, meeting notes
- "break" = intentional break, idle for most of the block
- "personal" = social media, shopping, entertainment, personal browsing
- Use "high" confidence when the signals clearly point to one project
- Use "medium" when it's likely but not certain
- Use "low" when you're guessing"""


def classify_block(
    http_client: httpx.Client,
    api_key: str,
    model: str,
    context: dict,
    screenshots: list[dict],
    projects: list[dict],
) -> dict | None:
    """Call the LLM via OpenRouter to classify a single block."""
    prompt_text = _build_prompt(context, projects)

    # Build OpenAI-compatible message content: text + images
    content = []
    for ss in screenshots:
        b64 = _encode_screenshot(ss["filepath"])
        if b64:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                },
            })

    content.append({"type": "text", "text": prompt_text})

    try:
        response = http_client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 256,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"[peak] LLM API error: {e}")
        return None

    # Parse response
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        print(f"[peak] Unexpected API response structure: {json.dumps(data)[:300]}")
        return None

    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        print(f"[peak] Failed to parse LLM response: {text[:200]}")
        return None

    # Validate
    project_names = {p["name"] for p in projects}
    if result.get("project") not in project_names:
        print(f"[peak] LLM returned unknown project: {result.get('project')}")
        return None

    work_type = result.get("work_type", "shallow_work")
    if work_type not in VALID_WORK_TYPES:
        work_type = "shallow_work"
    result["work_type"] = work_type

    confidence = result.get("confidence", "medium")
    if confidence not in VALID_CONFIDENCE:
        confidence = "medium"
    result["confidence"] = confidence

    return result


class ClassificationPipeline:
    def __init__(self, config: dict):
        self.config = config
        self.interval = config.get("classification_interval_s", 300)
        self.model = config.get("openrouter_model", "google/gemini-3-flash-preview")
        self.api_key = config.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY", "")
        self._stop = threading.Event()
        self._http_client = None

    def _get_http_client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client()
        return self._http_client

    def start(self):
        """Main loop — runs until stopped."""
        if not self.api_key:
            print("[peak] WARNING: No OpenRouter API key configured. Set OPENROUTER_API_KEY env var or openrouter_api_key in config.json")
            return

        print(f"[peak] Classification pipeline started (interval={self.interval}s, model={self.model})")

        # Initial delay: wait for some data to accumulate
        self._stop.wait(30)

        while not self._stop.is_set():
            try:
                self._run_cycle()
            except Exception as e:
                print(f"[peak] Classification error: {e}")
                traceback.print_exc()
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._http_client:
            self._http_client.close()
        print("[peak] Classification pipeline stopped.")

    def _run_cycle(self):
        """One classification cycle: find and classify pending blocks."""
        conn = db.get_connection()
        try:
            projects = db.get_active_projects(conn)
            if not projects:
                print("[peak] No projects configured — skipping classification")
                return

            pending = _get_pending_blocks(conn)
            if not pending:
                return

            print(f"[peak] Classifying {len(pending)} blocks...")
            http_client = self._get_http_client()
            classified = 0

            skipped_idle = 0

            for block_dt in pending:
                if self._stop.is_set():
                    break

                block_start, block_end = _block_range(block_dt)
                context = gather_block_context(conn, block_start, block_end)
                if context.get("empty"):
                    continue

                # Shortcut: 0 active time across all machines → auto-classify as break
                if context.get("all_idle"):
                    db.insert_classification(
                        conn,
                        block_start=block_start,
                        block_end=block_end,
                        project="Break",
                        task=None,
                        work_type="break",
                        confidence="high",
                        classification_tier="rule",
                        active_minutes=0,
                    )
                    skipped_idle += 1
                    classified += 1
                    continue

                screenshots = select_screenshots(conn, block_start, block_end)
                result = classify_block(
                    http_client, self.api_key, self.model,
                    context, screenshots, projects,
                )

                if result:
                    db.insert_classification(
                        conn,
                        block_start=block_start,
                        block_end=block_end,
                        project=result["project"],
                        task=result.get("task"),
                        work_type=result["work_type"],
                        confidence=result["confidence"],
                        classification_tier="llm",
                        active_minutes=context["active_minutes"],
                    )
                    classified += 1

            if classified:
                llm_count = classified - skipped_idle
                print(f"[peak] Classified {classified}/{len(pending)} blocks ({skipped_idle} idle→rule, {llm_count} via LLM)")
        finally:
            conn.close()
