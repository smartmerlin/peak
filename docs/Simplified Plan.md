## What It Does

Captures what you're doing on your Windows machine and two Mac Minis (accessed via Parsec), classifies it by project using an LLM, and gives you daily/weekly summaries.

**In scope:**
- Window focus tracking on all machines (gapless timeline)
- Chrome tab tracking (treated as spans, same as window switches)
- Screenshots every 30s
- Idle detection (hybrid: event-driven for lock/sleep, polling for soft idle)
- Google Calendar + Asana integrations
- LLM classification (three tiers: rules → DB lookup → LLM)
- Web UI for viewing timeline + correcting mistakes
- Daily + weekly summaries

**Deferred:** Granola, Gmail, Claude Code logs, Whoop, real-time nudges, phone tracking, auto-DND, morning plans.

---

## Data Model

Six tables. `window_spans` is the source of truth. Everything else supports classification.

### window_spans

Every row is one continuous focus period. App switches AND browser tab switches both create new spans.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| start_time | TEXT (ISO 8601) | |
| end_time | TEXT (ISO 8601) | null if active |
| duration_ms | INTEGER | |
| app_name | TEXT | "Chrome", "VS Code", etc. |
| window_title | TEXT | window title or tab title |
| url | TEXT | for browser spans, null otherwise |
| machine | TEXT | "windows", "mac-1", "mac-2" |
| idle_during | BOOLEAN | |
| active_ms | INTEGER | duration minus idle |

New span created by: app focus change, tab switch, in-tab navigation. Idle doesn't create new spans — just updates `idle_during` and `active_ms` on the current one.

### enrichments

Signals that help classification but aren't focus events.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| timestamp | TEXT (ISO 8601) | |
| source | TEXT | "screenshot", "calendar", "asana" |
| data | TEXT (JSON) | varies by source |

### classifications

One row per 5-minute block.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| block_start | TEXT (ISO 8601) | |
| block_end | TEXT (ISO 8601) | |
| project | TEXT | from project list |
| task | TEXT | what you were doing |
| work_type | TEXT | deep_work, shallow_work, meeting, break, personal |
| confidence | TEXT | high, medium, low |
| classification_tier | TEXT | rule, db_lookup, llm |
| verified | BOOLEAN | user confirmed? |
| active_minutes | REAL | active time in block |

### corrections

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| classification_id | INTEGER FK | |
| original_project | TEXT | |
| corrected_project | TEXT | |
| original_task | TEXT | |
| corrected_task | TEXT | |
| corrected_work_type | TEXT | |
| signals_snapshot | TEXT (JSON) | raw signals for few-shot learning |
| created_at | TEXT (ISO 8601) | |

### rules

Deterministic classification rules extracted from repeated corrections.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| condition_type | TEXT | url_pattern, app_name, window_title_contains |
| condition_value | TEXT | the pattern |
| project | TEXT | |
| task_template | TEXT | optional |
| work_type | TEXT | |
| hit_count | INTEGER | |
| created_at | TEXT (ISO 8601) | |

### projects

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | |
| description | TEXT | disambiguation hints, grows over time |
| active | BOOLEAN | |

---

## Components

### Windows Agent

Python daemon. Two jobs:

**Focus tracking:** `SetWinEventHook` listens for `EVENT_SYSTEM_FOREGROUND`. On each event: close previous span (set `end_time`, compute `duration_ms`), open new span. Gets app name from PID via `psutil`, window title via `GetWindowText`. Writes directly to `window_spans`.

**Idle detection (hybrid):**
- *Event-driven:* `WTSRegisterSessionNotification` for lock/unlock, `SystemEvents` for sleep/wake. Fires instantly — immediately marks current span as idle on lock/sleep, resumes on unlock/wake.
- *Polling:* Separate thread checks `GetLastInputInfo()` every 5s for soft idle (mouse/keyboard inactive 120s+). Marks `idle_during = true`, subtracts idle time from `active_ms` on resume.

**Parsec (two Macs):** Two Mac Minis accessed via Parsec. When the Windows agent detects Parsec is focused, it stops classifying its own spans (it's just a remote viewer). The Mac agents handle their own timelines — each Mac agent knows its own identity (mac-1 or mac-2 from config) and reports spans in its JSONL file. Windows agent just attributes its screenshots to whichever Mac is currently active (i.e., whichever Mac agent most recently reported a non-idle span). Since mac-1 is always Project 1 and mac-2 is always Project 2, this acts as a built-in Tier 1 rule — no LLM needed for any Parsec time.

**Screenshots:** Every 30s, capture screen via `mss`, resize to 720p, save as JPEG. Pixel-diff flags `screen_changed`. Stored as enrichments. Retention: after 48h keep one per 5-min block, after 30 days delete.

### Mac Agent

Lightweight Python script. Same logic, different APIs.

**Focus tracking:** `NSWorkspaceDidActivateApplicationNotification` via `pyobjc`. Gets window title via AppleScript. If browser, grabs URL via AppleScript too.

**Idle detection (hybrid):**
- *Event-driven:* `NSWorkspaceScreensDidSleepNotification` / `DidWake`, `NSWorkspaceSessionDidResignActiveNotification` for screen lock. Instant detection.
- *Polling:* `CGEventSourceSecondsSinceLastEventType` via `Quartz`, same 5s / 120s threshold for soft idle.

**Output:** Each Mac agent writes JSONL to a synced folder, tagged with its machine ID (mac-1, mac-2). Windows agent tails these files (tracks byte offsets), ingests new lines into `window_spans`.

### Chrome Extension

Listens for `tabs.onActivated`, `windows.onFocusChanged`, `tabs.onUpdated`. On each event, sends `{timestamp, tab_title, url}` to `http://localhost:7834/chrome-event`. The local agent receives it and creates a new span in `window_spans` (closing the previous one). On Mac, writes to the JSONL file instead.

Configurable URL blocklist for sensitive domains.

### Google Calendar

Polls every 15 min (and on startup). Stores events as enrichments. Classification pipeline uses meeting blocks to attribute idle time during meetings.

### Asana

Polls every 5 min for completed tasks. Stores as enrichments. Strong signal for project attribution on surrounding blocks.

---

## Classification Pipeline

Runs every 5 minutes. For each block:

**1. Gather context.** Query `window_spans` for the block's time range. Compute dominant app/title, total active/idle time, app switches. Pull enrichments (screenshots where `screen_changed = true`, calendar events, asana completions).

**2. Tier 1 — Rules.** Check `rules` table against dominant signals. Match → classify instantly, no LLM call.

**3. Tier 2 — DB lookup.** Find past verified classifications with similar app/title. If one project clearly dominates (3+ matches, 2x the runner-up) → use it.

**4. Tier 3 — LLM.** Send to Claude Sonnet: project list with descriptions, block context, 2-3 screenshots, relevant past corrections as few-shot examples. Returns project, task, work_type, confidence.

Cost: ~$0.01-0.03 per LLM-classified block. If 50% hit the LLM, ~$2-4/day.

---

## Web UI

Flask + vanilla HTML/JS on `localhost:7835`.

**Timeline view:** Day's 5-minute blocks, color-coded by project. Low-confidence flagged with warning. Stats bar at top (active time, deep work, meetings, etc.).

**Correction flow:** Click block → modal with current classification + raw signals (window titles, screenshot, URL). Dropdowns for project/work type, editable task field. Save writes correction, marks verified.

**Bulk verify:** One button to verify all high-confidence blocks.

---

## Summaries

**Daily (end of day):** Sonnet API call with day's classified blocks. Outputs: time breakdown by project, top tasks, distraction events with cost estimate, context switching stats, one improvement suggestion. Pushed via desktop notification.

**Weekly (Sunday/Monday):** Opus API call with week's data. Outputs: project time vs. targets, deep work report, fragmentation scores per project, distraction patterns, meeting load, system accuracy stats, one suggested change for next week.

---

## Self-Improvement Loop

Runs weekly alongside summary generation:

1. **Rule extraction:** 3+ consistent corrections for the same pattern → propose new rule for user approval.
2. **Project description enrichment:** Suggest additions based on verified classification signals.
3. **Accuracy tracking:** Log correction rate per tier, flag degradation.

---

## Build Order

Each phase is independently usable.

### Phase 1: Capture
- [ ] SQLite schema (all 6 tables)
- [ ] Windows agent: focus tracking + hybrid idle detection (events + polling) → writes to `window_spans`
- [ ] Screenshot capture → writes to `enrichments`
- [ ] Mac agents (x2): focus tracking + hybrid idle detection → JSONL output with machine ID
- [ ] Windows agent ingests Mac JSONL files into `window_spans`
- [ ] Parsec detection: map Parsec window titles → mac-1/mac-2
- [ ] Chrome extension → sends tab events to agent → creates spans
- [ ] Verify: gapless timeline in `window_spans` for all machines

### Phase 2: Classification
- [ ] Project list in DB
- [ ] LLM classification (Tier 3 only) every 5 minutes
- [ ] Screenshot selection (2-3 per block)
- [ ] Write classifications to DB
- [ ] Verify: spot-check classifications for a day

### Phase 3: UI + Corrections
- [ ] Flask app with timeline view
- [ ] Click-to-correct modal
- [ ] Bulk verify
- [ ] Stats bar

**Usable here.** You can capture, classify, and correct. Everything after this makes it better.

### Phase 4: Integrations
- [ ] Google Calendar (OAuth + polling → enrichments)
- [ ] Asana (polling → enrichments)
- [ ] Classification pipeline uses calendar + Asana signals
- [ ] Calendar/Asana shown on timeline

### Phase 5: Intelligence
- [ ] Tier 1 rules engine
- [ ] Tier 2 DB lookup
- [ ] Auto-rule proposals from corrections
- [ ] Daily summary generation + push
- [ ] Weekly summary generation
- [ ] Screenshot retention cleanup
