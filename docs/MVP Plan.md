## What the MVP Does

A system that runs continuously on your Windows machine and Mac Mini, captures what you're doing, classifies it by project and task using an LLM, and delivers daily/weekly summaries with actionable insights.

**MVP scope — what's in:**

- Windows agent: event-driven window focus tracking (gapless timeline), screenshots, idle detection
- Mac agent: event-driven window focus tracking, idle detection, synced via shared file
- Chrome extension: tab tracking with timestamps (both machines)
- Google Calendar integration (meeting blocks)
- Asana integration (task completions)
- LLM classification pipeline (5-minute batches)
- SQLite storage with three-tier classification (rules → DB lookup → LLM)
- Correction UI (simple web timeline)
- Daily summary pushed to you
- Weekly summary report
- Screenshot retention policy

**MVP scope — what's deferred to v2:**

- Granola integration (meeting transcripts)
- Gmail integration
- Claude Code log parsing
- Whoop integration
- Real-time rabbit hole nudges
- Delegation/LLM-can-do-this suggestions
- Intent vs. actual comparison
- Phone activity tracking
- Automated DND/Focus mode triggering
- Start-of-day AI-generated plan

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   WINDOWS MACHINE                    │
│                   (Primary Hub)                      │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │  Win Agent    │  │  Screenshot  │  │  Chrome    │ │
│  │  - focus      │  │  Capture     │  │  Extension │ │
│  │    events     │  │  - every 30s │  │  - tab     │ │
│  │  - idle       │  │  - 720p jpg  │  │    changes │ │
│  │  - gapless    │  │              │  │            │ │
│  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘ │
│         │                 │                 │        │
│         ▼                 ▼                 ▼        │
│  ┌────────────────────────────────────────────────┐  │
│  │              SQLite Database                    │  │
│  │  observations | classifications | corrections  │  │
│  │  rules | projects | daily_summaries            │  │
│  └────────────────────┬───────────────────────────┘  │
│                       │                              │
│         ┌─────────────┼─────────────┐                │
│         ▼             ▼             ▼                │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐       │
│  │ Tier 1   │  │ Tier 2   │  │ Tier 3       │       │
│  │ Rules    │  │ DB       │  │ LLM (Claude  │       │
│  │ Engine   │  │ Lookup   │  │ Sonnet API)  │       │
│  └──────────┘  └──────────┘  └──────────────┘       │
│                       │                              │
│                       ▼                              │
│  ┌────────────────────────────────────────────────┐  │
│  │  Local Web UI (Flask + simple HTML/JS)          │  │
│  │  - Daily timeline with corrections              │  │
│  │  - Weekly dashboard                             │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │  External Integrations                          │  │
│  │  - Google Calendar API (polling)                │  │
│  │  - Asana API (polling)                          │  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                    MAC MINI                           │
│                                                      │
│  ┌──────────────┐  ┌────────────┐                    │
│  │  Mac Agent    │  │  Chrome    │                    │
│  │  - focus      │  │  Extension │                    │
│  │    events     │  │  - tab     │                    │
│  │  - idle       │  │    changes │                    │
│  │  - gapless    │  │            │                    │
│  └──────┬───────┘  └─────┬──────┘                    │
│         │                │                           │
│         ▼                ▼                           │
│  ┌────────────────────────────────────────────────┐  │
│  │  Local JSONL file → synced to shared folder     │  │
│  │  (OneDrive / Dropbox / iCloud)                  │  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## Data Model (SQLite)

### observations

The raw capture data. Every signal gets a row.

|Column|Type|Description|
|---|---|---|
|id|INTEGER PK|Auto-increment|
|timestamp|TEXT (ISO 8601)|When the observation was captured|
|source|TEXT|"win_agent", "mac_agent", "chrome_ext", "screenshot", "calendar", "asana"|
|machine|TEXT|"windows" or "mac"|
|data|TEXT (JSON)|Payload varies by source type|

**Payload examples by source:**

```json
// win_agent / mac_agent — logged on every focus change event
{"app_name": "Code", "window_title": "main.py - my-project", "event": "focus_start"}
{"app_name": "Code", "window_title": "main.py - my-project", "event": "focus_end", "duration_ms": 857412}

// chrome_ext
{"tab_title": "Figma - Client X Rebrand", "url": "https://figma.com/file/abc", "machine": "mac"}

// screenshot
{"filepath": "/screenshots/2025-02-22/143022.jpg", "screen_changed": true}

// calendar
{"event_id": "abc123", "title": "Sprint Planning", "start": "...", "end": "...", "attendees": [...]}

// asana
{"task_id": "456", "task_name": "Fix checkout bug", "project": "Project X", "action": "completed", "completed_at": "..."}

// idle transition — logged when idle state changes
{"event": "idle_start", "last_input_seconds_ago": 120}
{"event": "idle_end", "idle_duration_ms": 340000}
```

### window_spans

Derived from observation events. A complete, gapless timeline of what was in focus. Each row represents one continuous span where a single window held focus.

|Column|Type|Description|
|---|---|---|
|id|INTEGER PK|Auto-increment|
|start_time|TEXT (ISO 8601)|When this window gained focus|
|end_time|TEXT (ISO 8601)|When this window lost focus (null if currently active)|
|duration_ms|INTEGER|Computed duration in milliseconds|
|app_name|TEXT|Application name (e.g., "Code", "Chrome", "Parsec")|
|window_title|TEXT|Full window title at time of focus|
|machine|TEXT|"windows" or "mac"|
|idle_during|BOOLEAN|Whether an idle period occurred during this span|
|active_ms|INTEGER|Actual active time within span (duration minus any idle periods)|

This table is the **primary source of truth** for time attribution. Every millisecond of computer use maps to exactly one row. The classification pipeline reads from this table rather than raw observations.

### classifications

The output of the classification pipeline. One row per 5-minute block.

|Column|Type|Description|
|---|---|---|
|id|INTEGER PK|Auto-increment|
|block_start|TEXT (ISO 8601)|Start of 5-min block|
|block_end|TEXT (ISO 8601)|End of 5-min block|
|project|TEXT|From your MECE project list|
|task|TEXT|High-level description of the discrete unit of work|
|work_type|TEXT|"deep_work", "shallow_work", "meeting", "break", "personal"|
|confidence|TEXT|"high", "medium", "low"|
|classification_tier|TEXT|"rule", "db_lookup", "llm"|
|verified|BOOLEAN|Has the user confirmed/corrected this? Default false|
|active_minutes|REAL|Minutes of actual activity within this block (computed from window_spans active_ms)|
|notes|TEXT|Any additional context from the classifier|

### corrections

Every time the user fixes a classification.

|Column|Type|Description|
|---|---|---|
|id|INTEGER PK|Auto-increment|
|classification_id|INTEGER FK|Points to the classification that was corrected|
|original_project|TEXT|What the system said|
|original_task|TEXT|What the system said|
|corrected_project|TEXT|What the user said|
|corrected_task|TEXT|What the user said|
|corrected_work_type|TEXT|If changed|
|signals_snapshot|TEXT (JSON)|The raw signals that were present (for future few-shot use)|
|created_at|TEXT (ISO 8601)|When the correction was made|

### rules

Deterministic classification rules extracted from corrections.

|Column|Type|Description|
|---|---|---|
|id|INTEGER PK|Auto-increment|
|condition_type|TEXT|"url_pattern", "app_name", "window_title_contains", "asana_project"|
|condition_value|TEXT|The pattern to match|
|project|TEXT|Assigned project|
|task_template|TEXT|Optional task description template|
|work_type|TEXT|Assigned work type|
|hit_count|INTEGER|How many times this rule has matched|
|created_at|TEXT (ISO 8601)|When the rule was created|

### projects

Your MECE project list.

|Column|Type|Description|
|---|---|---|
|id|INTEGER PK|Auto-increment|
|name|TEXT|Project name|
|description|TEXT|Rich description with disambiguation hints (grows over time)|
|active|BOOLEAN|Is this project currently active?|

---

## Component Specifications

### 1. Windows Agent (Python daemon)

**Event-driven window focus tracking (not polling):**

Uses `SetWinEventHook` (via `pywin32` / `ctypes`) to listen for `EVENT_SYSTEM_FOREGROUND` events. Every time a window gains focus, the agent:

1. Closes the previous span: writes `focus_end` observation with computed duration, updates the `window_spans` row with `end_time` and `duration_ms`.
2. Opens a new span: writes `focus_start` observation, creates a new `window_spans` row with `start_time`, `app_name` (from the window's PID via `psutil`), and `window_title` (via `GetWindowText`).

This produces a **complete, gapless, millisecond-accurate timeline** of every window that held focus. No sampling, no missed switches. A 10-second Slack check that would be invisible to 30-second polling is captured precisely.

**Example output:**

```
10:00:00.000  focus_start  VS Code - main.py
10:14:23.412  focus_end    VS Code - main.py           (duration: 14m 23.4s)
10:14:23.412  focus_start  Chrome - Slack
10:14:51.891  focus_end    Chrome - Slack               (duration: 28.5s)
10:14:51.891  focus_start  VS Code - main.py
10:22:07.334  focus_end    VS Code - main.py            (duration: 7m 15.4s)
10:22:07.334  focus_start  Parsec
...
```

**Idle detection (layered on top of focus tracking):**

A separate lightweight thread checks `GetLastInputInfo()` every 5 seconds. When no mouse/keyboard input for 120+ seconds:

1. Logs `idle_start` observation.
2. Marks the current `window_spans` row: `idle_during = true`.
3. When input resumes: logs `idle_end` with `idle_duration_ms`, updates the span's `active_ms` (total duration minus idle time).

The current window span stays open during idle — you don't create a new span just because you stopped moving the mouse. But the `active_ms` field accurately reflects that you weren't actively using the window.

**Parsec detection:**

- When a focus event fires and the app is "Parsec", the agent sets `machine_context = "mac"` on the span. The classification pipeline knows to pull Mac agent data for this span's time window.

**Writes to:** SQLite `observations` table (raw events) and `window_spans` table (derived timeline) directly.

**Runs as:** A Python script launched at startup (Task Scheduler or a simple tray app). Minimal resource usage — only writes to DB on window switch events and idle transitions, not continuously.

### 2. Screenshot Capture (part of Windows Agent)

**Every 30 seconds:**

- Capture screen via `mss` or `Pillow` (ImageGrab)
- Resize to 720p max dimension
- Save as JPEG quality 70 to `/screenshots/YYYY-MM-DD/HHMMSS.jpg`
- Compare with previous screenshot using simple pixel diff (mean absolute difference). Store `screen_changed: true/false` in observation.

**Retention policy (runs daily):**

- Screenshots older than 48 hours: keep only one per 5-minute block (the one selected as most representative — highest screen_changed delta from previous)
- Screenshots older than 30 days: delete entirely

### 3. Mac Agent (lightweight Python script)

**Event-driven window focus tracking:**

Uses `NSWorkspace.sharedWorkspace().notificationCenter()` (via `pyobjc`) to listen for `NSWorkspaceDidActivateApplicationNotification`. On each event:

1. Logs the previous app's `focus_end` with duration.
2. Logs the new app's `focus_start` with app name.
3. Gets the active window title via AppleScript: `tell application "System Events" to get name of front window of (first application process whose frontmost is true)`.
4. If active app is Chrome/Arc/Safari, also grabs the current URL via AppleScript.

Produces the same gapless timeline as the Windows agent.

**Idle detection:**

Checks `CGEventSourceSecondsSinceLastEventType` (via `Quartz` / `pyobjc`) every 5 seconds. Same logic as Windows: logs `idle_start` / `idle_end` transitions, updates active time on the current span.

**Output:** Appends JSONL to a file in a synced folder (e.g., `~/Dropbox/time-tracker/mac-observations.jsonl`). One JSON object per line, timestamped. Each line is either a focus event or an idle transition — same schema as the Windows observations.

**The Windows agent** reads this file, ingests new lines since last read, and writes them to the SQLite `observations` table with `machine = "mac"` and populates the `window_spans` table. Simple file-tailing logic — track the byte offset of last read.

### 4. Chrome Extension (both machines)

**Listens for:**

- `chrome.tabs.onActivated` — user switched to a different tab
- `chrome.windows.onFocusChanged` — user switched to/from Chrome
- `chrome.tabs.onUpdated` with `changeInfo.title` — page finished loading / title changed

**On each event, logs:**

```json
{"timestamp": "ISO 8601", "tab_title": "...", "url": "...", "event": "activated|focused|updated"}
```

**Output:** Sends to a local HTTP endpoint on the respective machine (e.g., `http://localhost:7834/chrome-event`). The agent on that machine receives it and writes to observations. On the Mac, it writes to the JSONL file instead.

**Privacy consideration:** The extension should have a configurable URL blocklist — domains you never want logged (banking, health, etc.). Default includes common sensitive domains.

### 5. Calendar Integration

**Polling:** Every 15 minutes, fetch today's events from Google Calendar API. Also fetch once on agent startup for the full day.

**For each event, store:**

- Event ID, title, start/end times, attendees, location
- Whether it's marked as "busy" or "free"

**Used by the classification pipeline** to automatically attribute idle-on-computer time blocks that fall within meeting windows.

### 6. Asana Integration

**Polling:** Every 5 minutes, check for recently completed tasks assigned to you.

**For each completion, store:**

- Task ID, task name, project name, completion timestamp
- Parent task/section if available (for project hierarchy context)

**Used by the classification pipeline** as a strong signal: if an Asana task was completed at 10:43am in Project X, the surrounding activity blocks are very likely Project X work.

### 7. Classification Pipeline

**Runs every 5 minutes.** Processes all window spans that ended (or are still open) in the most recent 5-minute window.

**Step 1: Gather context for the block**

The pipeline reads from `window_spans` as the primary source of truth, enriched with other signals:

```python
block = {
    "start": "2025-02-22T10:30:00",
    "end": "2025-02-22T10:35:00",
    "window_spans": [
        # Complete, gapless timeline from window_spans table
        {"app": "VS Code", "title": "main.py - project-x", "start": "10:28:12", "end": "10:32:45", "active_ms": 273000},
        {"app": "Chrome", "title": "Slack - #engineering", "start": "10:32:45", "end": "10:33:18", "active_ms": 33000},
        {"app": "VS Code", "title": "main.py - project-x", "start": "10:33:18", "end": "10:36:02", "active_ms": 164000},
    ],
    "dominant_app": "VS Code",              # computed: app with most active_ms
    "dominant_title": "main.py - project-x", # computed: title with most active_ms
    "total_active_ms": 470000,               # sum of active_ms across spans
    "total_idle_ms": 30000,                  # idle time within the block
    "app_switches": 2,                       # number of focus changes
    "chrome_tabs": [...],                    # from chrome extension
    "screenshots": [...],                    # 2-3 selected where screen_changed=true
    "calendar_event": {...} or null,
    "asana_completions": [...],
    "machine": "windows" or "mac" or "mixed"
}
```

**Step 2: Tier 1 — Deterministic rules**

Check the `rules` table. If any rule matches the dominant signal in this block (e.g., URL pattern, app name), classify immediately without LLM call.

```python
for rule in rules_db.get_all_active():
    if rule.matches(block):
        return Classification(
            project=rule.project,
            task=rule.task_template,
            work_type=rule.work_type,
            tier="rule",
            confidence="high"
        )
```

**Step 3: Tier 2 — Database lookup**

Query past verified classifications with similar signals:

```python
similar = db.query("""
    SELECT c.project, c.task, c.work_type, COUNT(*) as freq
    FROM classifications c
    JOIN window_spans ws ON ws.start_time >= c.block_start AND ws.end_time <= c.block_end
    WHERE c.verified = true
      AND (ws.app_name = {current_dominant_app}
           OR ws.window_title LIKE {current_dominant_title_pattern})
    GROUP BY c.project, c.task, c.work_type
    ORDER BY freq DESC
    LIMIT 5
""")

if similar and similar[0].freq >= 3 and similar[0].freq > similar[1].freq * 2:
    # Clear winner — use it
    return Classification(..., tier="db_lookup", confidence="high")
```

**Step 4: Tier 3 — LLM classification**

Call Claude Sonnet API with:

```
System: You are a time-tracking classifier. Given the following observations
from a 5-minute work block, classify what the user was doing.

Projects (pick exactly one):
{project_list_with_descriptions}

Work types: deep_work, shallow_work, meeting, break, personal

Here are some past corrections for similar signals (learn from these):
{top_5_relevant_corrections}

Respond in JSON:
{
  "project": "...",
  "task": "short description of the specific work being done",
  "work_type": "...",
  "confidence": "high|medium|low",
  "reasoning": "brief explanation"
}

User: [block context including 2-3 screenshots, window spans with durations,
chrome tabs, calendar event if any, asana completions if any]
```

**Screenshot selection for LLM:** Don't send all 10 screenshots from the block. Send 2-3: the first one, the last one, and one from the middle where `screen_changed=true`. This is enough for the LLM to understand what happened while keeping costs low.

**Estimated cost:** ~$0.01-0.03 per 5-min block with Sonnet (mostly image tokens). If 50% of blocks hit the LLM, that's ~$2-4/day.

### 8. Correction UI (Local Web App)

**Stack:** Flask backend + vanilla HTML/JS frontend. Nothing fancy. Runs on `localhost:7835`.

**Daily Timeline View:**

```
Today: Saturday Feb 22, 2025
Active: 7h 12m | Deep work: 3h 45m | Shallow: 1h 30m | Meetings: 2h | Breaks: 47m

 8:00 ━━━━━━━━━━━━━━━━ Project X — Writing API spec (deep) ████████
 8:35 ━━━━━━━━━━━━━━━━ Project X — Writing API spec (deep) ████████
 9:00 ━━━━━━━━ ⚠️ Slack/Email (shallow) ████
 9:15 ━━━━━━━━━━━━━━━━ Sprint Planning (meeting) ████████
10:00 ━━━━━━━━━━━━━━━━ Sprint Planning (meeting) ████████
10:15 ━━━━━━━━━━━━━━━━ ⚠️ Project Y — Reviewing PR (deep) ████████
10:30 ━━━━━━━━ Break ░░░░
10:45 ━━━━━━━━━━━━━━━━ Project X — Debugging auth flow (deep) ████████
 ...

[⚠️ = low confidence, click to correct]
```

**Correction flow:**

1. Click any block on the timeline
2. Modal shows: current classification, the raw signals (window titles, screenshot thumbnail, chrome tabs)
3. Dropdowns: Project (from your list), Work Type
4. Text field: Task description (pre-filled, editable)
5. Save → writes to `corrections` table, updates `classifications` row, marks as `verified = true`

**Bulk verify:** Button to "verify all high-confidence blocks" so you only manually review the uncertain ones.

### 9. Daily Summary (auto-generated, pushed to you)

**Runs at:** configurable time (e.g., 6pm or whenever you typically stop working). Triggered by schedule or by detecting extended idle at end of day.

**Generated by:** Claude Sonnet API call with the full day's classified blocks.

**Delivered via:** Desktop notification with a link to the web UI, plus optionally emailed or posted to a Slack DM.

**Contents:**

```
## Friday, Feb 22

**Active time:** 7h 12m across 8.5 hours
**Deep work:** 3h 45m (52% — target: 50%+ ✓)
**Meetings:** 2h 00m
**Shallow work:** 1h 30m
**Breaks/personal:** 47m

### By Project
- Project X: 3h 15m (1h 50m deep, 1h 25m meetings)
- Project Y: 2h 00m (1h 10m deep, 50m shallow)
- Admin/Other: 1h 10m (all shallow)
- Unclassified: 47m (3 low-confidence blocks — please review)

### Top Tasks Worked On
1. Writing API spec for Project X — 1h 40m deep work
2. Sprint Planning — 1h 00m meeting
3. Reviewing PRs for Project Y — 55m deep work
4. Slack/email triage — 1h 05m shallow

### Distraction Events
- 10:02am: Switched from API spec writing to Slack thread (non-urgent).
  Spent 12 min before returning. Total cost with ramp-up: ~20 min.
- 2:15pm: Browsed Twitter for 8 min during task transition.

### Context Switching
- Average uninterrupted block: 28 min
- Longest focus block: 52 min (API spec, 8:00-8:52)
- Project switches: 9 times

### Suggested Improvements
- Your morning deep work block (8:00-9:00) was your most productive.
  Consider protecting this window — you had Slack open by 9:02.
```

### 10. Weekly Summary (auto-generated, Sunday evening or Monday morning)

**Generated by:** Claude Opus API call with the full week's daily summaries + raw classification data.

**Contents:**

```
## Week of Feb 17-21

### Time Allocation
- Project X: 14.2h (target: 15h) — 95% on track
- Project Y: 8.5h (target: 6h) — over-invested by 2.5h
- Project Z: 2.1h (target: 5h) — significantly behind
- Admin/Other: 5.3h
- Meetings: 9.2h
- Total productive: 39.3h

### Deep Work Report
- Total deep work: 16.8h (43% of active time)
- Average daily deep work: 3.4h
- Best day: Tuesday (4.8h deep work)
- Worst day: Thursday (1.2h — 4 back-to-back meetings)
- Average uninterrupted block: 31 min (up from 26 min last week ✓)
- Longest block all week: 1h 22m (Wednesday morning, Project X)

### Fragmentation Score
- Project X: avg block 38 min (good — focused)
- Project Y: avg block 14 min (fragmented — 11 blocks across 5 days)
- Project Z: avg block 22 min

### Distraction Patterns
- Slack interrupted deep work 11 times → cost ~3.5h total with ramp-up
- 7 of 11 were non-urgent
- Most vulnerable time: 10-11am (5 of 11 interruptions)
- Twitter/news browsing: 1.8h total, mostly at task transitions

### Meetings
- 9.2h in meetings this week (23% of time)
- 3 meetings had no clear outcome/action items
- Thursday was 72% meetings — consider protecting that day

### System Accuracy
- Blocks classified: 432
- Auto-classified (rules + DB): 341 (79%)
- LLM-classified: 91 (21%)
- Corrections made: 8 (1.8% error rate)
- New rules generated: 3

### This Week's #1 Lever
Project Z is 3h behind target and Thursday's meeting load is the
bottleneck. You have a 3-hour open block Wednesday afternoon — consider
dedicating it entirely to Project Z with Slack DND enabled.
```

---

## User Journeys

### Journey 1: First-Time Setup (Day 0)

**Who:** You, setting up the system for the first time.

**Steps:**

1. **Define your project list.** Open the web UI, go to Settings → Projects. Add each project with a name and description. Include a "General / Admin" catchall. Include a "Personal / Break" category. This takes 10-15 minutes and is the most important setup step — the richer your descriptions, the better classification starts.
    
2. **Install the Windows agent.** Clone the repo, install dependencies (`pip install` a few packages), run `python agent.py`. It starts capturing immediately. Add it to Windows Task Scheduler for startup persistence.
    
3. **Install the Mac agent.** Same but lighter — `python mac_agent.py`. Point its output to a Dropbox/OneDrive folder. Confirm the Windows agent can read the synced file.
    
4. **Install the Chrome extension.** Load unpacked extension in Chrome on both machines. Grant the permissions it asks for (tab access). Configure URL blocklist if desired.
    
5. **Set up API keys.** In the web UI settings: add your Anthropic API key, Google Calendar OAuth, Asana personal access token.
    
6. **Verify it's working.** Open the web UI. You should see observations streaming in — window titles, app names, screenshots appearing. Switch between a few apps. Check that both Windows and Mac observations are arriving. The classification pipeline will start producing blocks after the first 5 minutes.
    
7. **Let it run for a day.** Don't try to correct everything on day one. Just let it accumulate data.
    

**Duration:** 30-45 minutes total. Most of this is the OAuth flows and writing project descriptions.

---

### Journey 2: Morning Start (Daily, 2 minutes)

**Who:** You, starting your workday.

**Time:** First thing in the morning, before you open Slack.

**What happens automatically:**

- The agents are already running (launched at startup)
- Calendar events for today are already fetched
- Yesterday's daily summary was generated and is waiting for you

**What you do:**

1. **Glance at yesterday's daily summary** (if you didn't review it last night). Takes 30 seconds. Note any corrections needed — you can fix them now or later.
    
2. **Mentally note your top priorities for the day.** (In v2, the system will generate a proposed plan. For MVP, this is manual.)
    
3. **Start working.** The system captures everything in the background. You don't interact with it during the day unless you choose to.
    

**Duration:** 1-2 minutes. Often just a glance at the summary notification.

---

### Journey 3: During the Workday (Passive — the system works, you don't)

**Who:** The system, running in the background.

**What's happening continuously (event-driven):**

- Every time you switch windows, both agents log the transition instantly — the previous window's span is closed with an exact duration, and the new window's span opens. This produces a gapless, millisecond-accurate timeline of your entire day.
- If you're in Parsec, the Mac agent captures the real window/app data while the Windows agent captures screenshots of the Mac screen.
- Chrome extension logs every tab switch on both machines.
- Idle transitions are logged the moment they happen (120s of no input → idle_start; input resumes → idle_end).

**What's happening every 30 seconds (polling):**

- Screenshots are captured and saved. Pixel-diff comparison flags whether the screen content changed.

**What's happening every 5 minutes:**

- Classification pipeline runs on the latest 5-minute block
- Reads from `window_spans` table for exact time-per-app breakdown
- Tier 1 rules are checked first (instant)
- If no rule matches, Tier 2 DB lookup (instant)
- If still uncertain, Tier 3 LLM call (2-3 seconds, you never notice)
- Classification is written to the database

**What's happening during meetings:**

- Calendar integration knows you're in a meeting
- Idle detection confirms you're not actively on the computer (or detects that you are — multitasking)
- The block is classified as a meeting, attributed to the right project based on meeting title and attendees

**What's happening when you complete an Asana task:**

- Asana polling picks it up within 5 minutes
- The surrounding time blocks get a strong signal for that project
- Classification confidence goes up

**You don't interact with any of this.** The system is invisible during your workday. No notifications, no pop-ups, no friction.

---

### Journey 4: End-of-Day Review (Daily, 5 minutes)

**Who:** You, wrapping up your workday.

**Trigger:** System detects extended idle (30+ minutes after 5pm) or it hits your configured end-of-day time. It generates the daily summary and pushes a notification.

**What you do:**

1. **Open the daily summary** (notification links to the web UI). Scan the high-level numbers: total active time, deep work hours, meeting hours. Takes 15 seconds.
    
2. **Scan the timeline.** Blocks are color-coded by project. Low-confidence blocks are flagged with ⚠️. Does it look roughly right? Most days, it will. Takes 30 seconds.
    
3. **Fix any low-confidence or incorrect blocks.** Click the flagged block. See the raw signals (screenshot, window titles). Pick the right project from the dropdown. Edit the task description if needed. Click save. Each correction takes about 5-10 seconds.
    
    _Week 1:_ You'll correct maybe 10-15 blocks per day. This feels tedious but it's training the system.
    
    _Week 3:_ You'll correct 2-3 blocks per day. Most things are right.
    
    _Week 6+:_ You'll correct maybe 1 block every few days. The review is mostly just scanning.
    
4. **Hit "Verify remaining"** to mark all uncorrected high-confidence blocks as verified. This feeds the Tier 2 DB lookup.
    
5. **Read the distraction events and context switching stats.** Note any patterns. No action needed — just awareness for now.
    

**Duration:** 5 minutes in week 1 (mostly corrections). 2 minutes by week 4. Under 1 minute by week 8.

---

### Journey 5: Weekly Review (Weekly, 15 minutes)

**Who:** You, Sunday evening or Monday morning.

**Trigger:** System auto-generates the weekly summary. You've blocked 15 minutes on your calendar for this (the system suggested it during setup).

**What you do:**

1. **Read the time allocation section.** How much time went to each project? Is this aligned with what you think your priorities are? This is the most important question of the week. If Project Z is your highest priority but got 2 hours while Project Y (which is cruising) got 8 hours, that's a red flag. Takes 2 minutes.
    
2. **Read the deep work report.** How many hours of actual deep focus work did you get? What was your best/worst day and why? Is the trend improving week-over-week? Takes 1 minute.
    
3. **Read the fragmentation scores.** Are your important projects getting sustained blocks or being nibbled to death by context switches? If Project X has an average block of 14 minutes, you're not doing real work on it — you're just touching it. Takes 1 minute.
    
4. **Read the distraction patterns.** What pulled you out of deep work? How much total time did it cost? Is it the same thing every week (probably Slack)? Takes 1 minute.
    
5. **Read the #1 lever recommendation.** This is the system's single most impactful suggestion for next week. Maybe it's "protect Tuesday mornings," maybe it's "batch your email to twice daily," maybe it's "you have too many meetings on Thursday." Takes 30 seconds.
    
6. **Decide on one change for next week.** Not five changes. One. Maybe you'll set Slack DND from 8-10am. Maybe you'll move a recurring meeting. Maybe you'll block two 2-hour focus sessions on your calendar. Write it down or add it to your calendar. Takes 2 minutes.
    
7. **Check system accuracy stats.** How many corrections did you make? Is the error rate declining? Any new rules that were auto-generated? This is housekeeping to make sure the system is improving. Takes 1 minute.
    

**Duration:** 10-15 minutes. This is the highest-ROI time you spend all week on personal productivity.

---

### Journey 6: Correcting a Misclassification (Ad hoc, 10 seconds)

**Who:** You, during the end-of-day review.

**Scenario:** The timeline shows a 10:15-10:45 block classified as "Project Y — Reviewing PR (deep work)" with medium confidence. But you remember — you were actually reviewing a PR for Project X that happened to be in the same repo.

**Steps:**

1. Click the block on the timeline.
2. A modal slides out showing:
    - Current classification: Project Y / Reviewing PR / deep_work / medium confidence
    - Raw signals: window titles (VS Code - pull-request-123.md), Chrome tabs (GitHub PR #456), screenshot thumbnail
    - Similar past classifications the system found
3. You click the Project dropdown → select "Project X"
4. You edit the task to "Reviewing auth PR #456"
5. You click Save.

**What happens behind the scenes:**

- Correction is stored in the `corrections` table with the full signal snapshot
- The classification row is updated with the correct values and marked `verified = true`
- Next time the system sees VS Code + that GitHub PR URL pattern, it checks the corrections and gets it right
- If this pattern repeats 3+ times, a deterministic rule is auto-suggested for your approval

**Duration:** 10 seconds per correction.

---

### Journey 7: Adding a New Project (Ad hoc, 2 minutes)

**Who:** You, when a new project starts.

**Scenario:** You're starting work on "Project Alpha" — a new client engagement. The system is currently classifying this work as "General / Admin" because it doesn't know about it.

**Steps:**

1. Open web UI → Settings → Projects
2. Click "Add Project"
3. Fill in:
    - Name: "Project Alpha"
    - Description: "New client engagement for Acme Corp. Work involves Figma designs (look for 'acme' or 'alpha' in URLs), Google Docs for proposals, and Asana tasks in the 'Project Alpha' Asana project. Key collaborators: Sarah, James."
4. Save.
5. Optionally: go back to today's timeline and correct any blocks that were misclassified as "General / Admin" that should be "Project Alpha." These corrections immediately seed the system's understanding.

**Duration:** 2 minutes. The richer the description, the faster the system learns.

---

### Journey 8: System Self-Improvement Loop (Automated, weekly)

**Who:** The system, running automatically as part of the weekly summary generation.

**What happens:**

1. **Rule extraction.** The system scans the `corrections` table for repeated patterns. If the user has corrected "Chrome + figma.com/file/xyz → Project Alpha" three or more times, it proposes a new rule:
    
    ```
    Proposed rule: When URL matches 'figma.com/file/xyz*', classify as Project Alpha.
    Based on: 4 consistent corrections.
    ```
    
    In the weekly summary, these are listed for your approval. You click "Accept" or "Reject" for each.
    
2. **Project description enrichment.** The system looks at which signals are associated with each project based on verified classifications, and suggests additions to project descriptions:
    
    ```
    Suggested update to "Project Alpha" description:
    Add: "Often involves the Asana board 'Alpha Sprint Board'
    and Google Docs with 'Acme' in the title."
    ```
    
3. **Accuracy tracking.** The system logs classification accuracy metrics (% corrected) per tier. If Tier 3 (LLM) accuracy is dropping for a specific project, it flags this so you can enrich the project description.
    
4. **Cost tracking.** Logs how many LLM API calls were made, total cost, and how the tier distribution is shifting over time. Ideally you see Tier 1+2 growing and Tier 3 shrinking.
    

---

## Implementation Order

Build in this sequence. Each phase is usable on its own.

### Phase 1: Core Capture (2-3 days)

Build and verify that raw data is flowing correctly.

- [ ] SQLite database setup with schema (observations, window_spans, and other tables)
- [ ] Windows agent: event-driven focus tracking via `SetWinEventHook` for `EVENT_SYSTEM_FOREGROUND`
- [ ] Windows agent: idle detection thread (check `GetLastInputInfo` every 5s, log transitions)
- [ ] Windows agent: `window_spans` table populated on every focus change with gapless timeline
- [ ] Screenshot capture (30s interval, 720p JPEG, with pixel-diff comparison) — this remains polling-based
- [ ] Mac agent: event-driven focus tracking via `NSWorkspaceDidActivateApplicationNotification`
- [ ] Mac agent: idle detection via `CGEventSourceSecondsSinceLastEventType`
- [ ] Mac agent: JSONL output with focus events and idle transitions
- [ ] File sync setup (pick your shared folder)
- [ ] Windows agent ingests Mac JSONL file and populates `window_spans` for Mac events
- [ ] Parsec detection (when focus event fires for Parsec, flag spans as `machine_context = "mac"`)
- [ ] Verify: run for an hour, check that `window_spans` table has a complete gapless timeline with accurate durations for both machines

### Phase 2: Classification Pipeline (2-3 days)

Get the core classification working, even if rough.

- [ ] Project list configuration (simple JSON file or DB table, manual entry)
- [ ] Tier 3 only: LLM classification every 5 minutes
- [ ] Screenshot selection logic (pick 2-3 per block where screen changed)
- [ ] Basic prompt engineering: project list + block context + screenshots → classification
- [ ] Write classifications to DB
- [ ] Verify: run for a day, manually spot-check classifications in the DB

### Phase 3: Correction UI (2-3 days)

Make it usable by a human.

- [ ] Flask web app scaffold
- [ ] Daily timeline view (color-coded blocks, confidence flags)
- [ ] Click-to-correct modal (project dropdown, task edit, work type)
- [ ] Corrections written to DB
- [ ] "Verify all high-confidence" bulk action
- [ ] Basic stats bar (total active time, deep work hours, etc.)

### Phase 4: Integrations (2-3 days)

Layer on the external signals.

- [ ] Google Calendar integration (OAuth + polling)
- [ ] Asana integration (personal access token + polling)
- [ ] Chrome extension (tab tracking, sends to local endpoint)
- [ ] Calendar events shown on timeline as overlay blocks
- [ ] Asana completions shown as markers on timeline
- [ ] Classification pipeline uses calendar + Asana as signals

### Phase 5: Intelligence Layer (2-3 days)

Add the smart tiers and summaries.

- [ ] Tier 1: Deterministic rules engine + rules DB table
- [ ] Tier 2: DB lookup of similar verified classifications
- [ ] Auto-rule proposal from repeated corrections
- [ ] Daily summary generation (Sonnet API call at end of day)
- [ ] Daily summary push (desktop notification + optional email/Slack)
- [ ] Weekly summary generation (Opus API call)
- [ ] Distraction detection logic (deep work interrupted by shallow)
- [ ] Context switching / fragmentation calculation
- [ ] Screenshot retention policy (automated cleanup)

### Phase 6: Polish (ongoing)

- [ ] Weekly review dashboard in the web UI (charts, trends, week-over-week comparison)
- [ ] Project description enrichment suggestions
- [ ] System accuracy and cost dashboards
- [ ] Tray app for the Windows agent (icon, quick status, pause/resume)

---

## Total MVP Timeline

**Phases 1-5: ~2-3 weeks** if you're building it with Claude Code and dedicating a few hours per day. Phase 1 and 2 should be done first and you should live with them for a few days before building the rest — you'll learn a lot about what signals matter and what the LLM gets wrong.

**Phase 6** is ongoing polish that never really ends.

**The system is genuinely usable after Phase 3** — you have capture, classification, and a way to see/correct results. Everything after that makes it better but isn't required to get value.