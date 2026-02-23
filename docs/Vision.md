## The Problem

You spend 99% of your working day on a computer, split across a Windows machine and a Mac Mini (via Parsec). At the end of each week, you can't confidently answer: "Did I spend my time on the highest-impact work?" You have a vague sense of being busy, but no visibility into where the hours actually went, what pulled you off track, or whether your time allocation matches your stated priorities.

This isn't a discipline problem. It's an information problem. You can't optimize what you can't see.

## The Goal

Build a personal time intelligence system that gives you complete, accurate, effortless visibility into how you spend your working hours — and over time, actively helps you spend them better.

The system should feel like having a perfect executive assistant who watches your entire workday, remembers everything, and gives you a clear, honest debrief at the end of each day and week — with specific, actionable recommendations for improvement.

## Classification Dimensions

Every unit of time gets classified across three dimensions:

**1. Project** — from a MECE list you define and maintain. Every minute maps to exactly one project. Includes a "General / Admin" catchall and a "Personal / Break" category. You update this list as projects start and end.

**2. Task** — a high-level description of the discrete unit of work within that project. Not an Asana task necessarily — just something descriptive enough that when you scan a week of tasks, you can immediately recall what you were doing. Examples: "Writing API spec," "Debugging auth flow," "Reviewing Sarah's PR," "Sprint planning discussion."

**3. Work Type** — the nature of the work:

- **Deep work**: coding, writing, designing, thinking, problem-solving. Requires sustained focus.
- **Shallow work**: email, Slack, admin, scheduling, quick responses. Can be done in fragments.
- **Meetings**: synchronous time with other people.
- **Break / personal**: away from work, personal browsing, etc.

## Data Sources

The system draws from multiple signal sources, layered in priority:

**Primary (always-on, event-driven):**

- Window focus events on both machines (gapless, millisecond-accurate timeline)
- Idle/active state transitions (mouse + keyboard input detection)
- Chrome tab switches with URLs (extension on both machines)
- Screenshots every 30 seconds (from Windows, which captures Mac screen when in Parsec)

**Secondary (periodic polling):**

- Google Calendar (meeting blocks, attendees)
- Asana (task completions with project attribution)

**Planned for v2:**

- Granola (timestamped meeting transcripts — enables understanding what was discussed, not just that a meeting happened)
- Gmail (sent emails — timing, recipients, subject lines)
- Claude Code session logs (what code was written, what projects were touched)
- Whoop (sleep, recovery, workout data)
- Phone activity (at minimum Slack/email app usage via their APIs)

## How the System Learns and Improves

The system does not fine-tune the LLM. Instead, accuracy improves through three mechanisms that compound over time:

**Mechanism 1: Correction data becomes few-shot context.** Every correction you make is stored with the full signal snapshot. When classifying a new block, the system retrieves the most relevant past corrections (by matching app, URL, window title patterns) and includes them as examples in the LLM prompt. The LLM immediately avoids making the same mistake.

**Mechanism 2: Project descriptions get richer.** Your initial project descriptions are sparse. Over time, based on corrections and verified classifications, the system suggests additions: "Project Alpha is often associated with figma.com URLs containing 'acme' and Asana tasks in the 'Alpha Sprint Board.'" These enriched descriptions are part of every classification prompt, reducing ambiguity for the LLM.

**Mechanism 3: Deterministic rules absorb solved problems.** When the same correction pattern appears 3+ times, the system extracts a deterministic rule (e.g., "URL matches figma.com/file/xyz → Project Alpha"). Rules are proposed in the weekly summary for your approval. Once accepted, these patterns are classified instantly without any LLM call. Over weeks, more and more classifications migrate from LLM → DB lookup → deterministic rules. The LLM only handles genuinely novel or ambiguous situations.

**Expected accuracy trajectory:**

- Week 1: ~80-85% accurate. You correct 10-15 blocks per day.
- Week 3: ~93-95% accurate. You correct 2-3 blocks per day.
- Week 6+: ~97%+ accurate. You correct maybe 1 block every few days, usually for genuinely new situations.

## Post-MVP Feature Roadmap

These features are deferred from the MVP but represent the full end-state vision. Roughly ordered by expected impact.

### Tier 1: High Impact, Build Within First Month

**Whoop Integration — Energy-Aware Planning**

Pull daily sleep (hours, quality, REM/deep percentages), recovery score, strain, and workout data from Whoop's API. The system correlates physical state with cognitive output over time. After a few weeks, it can show insights like:

- "Your average deep work block on days with 7+ hours of sleep: 68 minutes. On days with less than 6 hours: 31 minutes."
- "Your three most productive days this month all followed recovery scores above 80."
- "Weeks where you hit 3 workouts: average 22 hours of productive work. Weeks with 0-1 workouts: average 16 hours."

This reframes sleep and exercise from aspirational goals into concrete ROI decisions. The morning plan can factor in your recovery score: "Recovery is 38% today. Front-load your most important task into the first 90 minutes, then plan for shallow work in the afternoon." Versus on a great day: "Recovery 92%. You have a 3-hour open block — use it for that hard design problem you've been deferring."

Weekly scorecard tracks your goals directly: Sleep ≥7hrs (5/7 nights ✓), Workouts (2/3 — you skipped Thursday). The system spots patterns: "You tend to skip workouts when you have morning meetings before 9am. Next week, consider moving workouts to Tuesday/Thursday."

**Start-of-Day AI-Generated Plan**

Every morning, the system generates a proposed daily plan using your calendar, Asana tasks, project priorities, and (with Whoop) your physical state. You spend 2 minutes confirming or adjusting. This does three things:

1. Creates an _intent_ baseline — the system now knows what you planned, making every other feature (distraction detection, end-of-day comparison) dramatically better.
2. Commitment effect — once you've said "I'm doing Project X from 9-11," you're psychologically more likely to do it.
3. Surfaces conflicts early — "You have 6 hours of meetings tomorrow. Your weekly deep work on Project X is already behind plan. Consider declining something."

**Intent vs. Actual Comparison**

With the morning plan established, the daily summary includes a comparison: "You planned 3 hours of deep work on Project X but got 47 minutes before being pulled into Slack and email." Over weeks, this reveals your systematic planning biases — maybe you consistently overestimate available focus time, or underestimate meeting overhead.

**Granola Integration — Meeting Intelligence**

Granola provides timestamped meeting transcripts. This enables the system to understand not just that you had a meeting, but what it was about, which projects were discussed, and how much time was spent on each topic within the meeting. A 1-hour "team sync" that spent 40 minutes on Project X and 20 minutes on Project Y can be split accordingly rather than being treated as a single block.

### Tier 2: High Impact, More Complex to Build

**Real-Time Rabbit Hole Nudges**

The one case where real-time interruption is justified. When the system detects you've been deep on something for longer than seems proportional to its importance, it surfaces a gentle question — not a command.

Design constraints:

- Only triggers if you've been on a task 30+ minutes (shorter for low-priority/shallow work, much longer for high-priority deep work)
- Only nudges if the task's priority doesn't warrant the time investment
- Maximum one nudge per 2 hours
- Only when you've had recent context switches (so the interruption cost is low — don't break a genuine flow state)
- Phrased as a question: "You've been refining this slide deck for 50 minutes. Is this the right level of polish given the audience?"

The goal is making the unconscious conscious. Half the time you'll think "oh crap, thanks" and half the time you'll think "yeah, this actually matters" — both are fine.

**Delegation and LLM-Can-Do-This Suggestions**

When the system detects you spent significant time on something that could have been delegated to a team member or done by an LLM, it includes this in the daily summary — not as a real-time interruption.

For delegation: requires a team roster with roles and areas of ownership. The system flags "this task falls in [person]'s area" and drafts the handoff — a ready-to-send Asana task or Slack message with context. The friction of delegation is the main reason people don't do it; removing that friction is the real value.

For LLM automation: "You spent 35 minutes formatting that spreadsheet. Next time, Claude can do this in ~2 minutes — here's how you'd prompt it."

The powerful version: the system doesn't just suggest, it _preps_ the delegation. "Here's a ready-to-send Asana task to hand this off to [person] next time." One click and it's done.

**Proactive Calendar Defense**

The system analyzes your upcoming calendar and flags problems before they happen:

- "Tomorrow you have meetings at 9, 10:30, 11:15, and 1. Your longest uninterrupted block is 45 minutes. Based on your data, you need 90+ minute blocks for deep work. Consider moving the 10:30."
- "Thursday is 72% meetings. Your weekly deep work on Project X is behind plan. Decline or delegate something."
- "Your Tuesday and Thursday mornings are consistently your best deep work windows, but you've been scheduling non-critical meetings in them."

### Tier 3: Nice-to-Have, Build When Core Is Solid

**Automated DND/Focus Mode Triggering**

Based on your morning plan and calendar, the system automatically enables Windows Focus Assist and macOS Focus modes during planned deep work blocks. Slack DND activates. Notifications are suppressed. When the block ends, everything returns to normal.

**Separate Browser Profiles**

The system prompts you to switch to a "deep work" browser profile (no social media, no email tabs) when entering a focus block. Or at minimum, flags when you open distracting tabs during planned deep work.

**Phone Activity Tracking**

Most phone "work" (Slack, email) can be detected through their server-side APIs rather than needing on-device tracking. The system can infer "he sent 4 Slack messages between 3:12-3:15 but was idle on both computers" → phone usage. For total phone screen time, iOS Screen Time summaries can be manually entered or pulled if automation is feasible.

**Gmail Integration**

Log sent emails with timestamps, recipients, and subject lines. Useful for attribution ("email to client X at 2:15pm → Project X, shallow work") and for the delegation analysis ("you sent 12 status update emails this week — could a team member own these?").

**Claude Code Log Parsing**

Claude Code saves session logs. Parse these to understand what coding work was done, which files/projects were touched, and how much time was spent in active coding sessions. Strong signal for project attribution and deep work classification.

## Distraction Analysis — How It Works

The system doesn't just count distracted minutes. It understands the _structure_ of distractions.

**What gets flagged as a distraction:** A distraction is when you switch from deep work on a high-priority project to shallow work (Slack, email, Twitter, news) without the switch being triggered by something time-sensitive. The system distinguishes:

- External interruption (Slack notification pulled you out) vs. self-initiated (you opened Twitter at a task boundary)
- Urgent interruption (production down, boss DM) vs. non-urgent (casual thread, newsletter)
- The _cost_ of the distraction includes ramp-up time — how long after returning to the original task before you're back at the same productivity level (estimated from typing speed, click patterns, or just a flat 5-10 minute penalty based on research)

**Distraction patterns surfaced in weekly review:**

- Total time lost to unplanned distractions and estimated ramp-up cost
- Most vulnerable times of day (usually 10-11am and post-lunch)
- Most common distraction sources (usually Slack)
- What percentage were non-urgent
- Distraction frequency during deep work vs. shallow work
- Trend line: is this improving week-over-week?

**Actionable recommendations:**

- "Your most productive deep work happens 7-9am before Slack volume picks up. Consider making this a protected block."
- "11 Slack interruptions during deep work this week, 7 non-urgent. Setting DND during focus blocks would reclaim ~3.5 hours."
- "You lost 40 minutes to unfocused browsing at task transition points. The system can surface your next planned task immediately when you complete something."

## Context Switching and Fragmentation — How It's Measured

**Context switches** are counted each time you move from one project to another (not just one app to another — switching from VS Code on Project X to VS Code on Project X's tests is not a context switch; switching from Project X to answering a Slack question about Project Y is).

**Fragmentation score per project:** Average length of uninterrupted blocks spent on that project. A project with 6 hours total but an average block of 14 minutes is being nibbled to death. A project with 4 hours total but an average block of 55 minutes is getting genuine focused attention.

**Daily and weekly metrics:**

- Total project switches per day
- Average uninterrupted block length (overall and per project)
- Longest focus block of the day/week
- Percentage of deep work time in blocks longer than 45 minutes (a proxy for "real deep work" vs. fragmented attempts)

## Making Distraction Costs Viscerally Clear

Abstract numbers don't always motivate. The system frames costs in terms that hit harder:

- "You lost approximately 4 hours to unplanned distractions this week. At your effective hourly rate, that's $X. That's also almost exactly the time you said you needed for [that project you keep saying you don't have time for]."
- "Your deep work this week was 43% of active time. Your target is 50%. The gap is 2.8 hours — almost entirely attributable to Slack interruptions during morning focus blocks."

Framing the cost as the specific thing you _wish_ you had time for is more motivating than abstract hours.

## The Transition Moment Problem

One of the biggest time sinks isn't during work — it's _between_ tasks. You finish something, you don't have a clear next action, you open Slack or Twitter "just to check," and 15 minutes evaporate.

The system addresses this by surfacing the next planned task immediately when it detects you're wrapping up (Asana task completed, activity patterns suggest you're finishing). No gap, no decision fatigue, no "let me just check Slack first." This is one of the highest-leverage features despite being simple — it eliminates the vacuum that distractions fill.

## Energy Management, Not Just Time Management

With Whoop data integrated, the system shifts from "how did you spend your hours" to "how well did you allocate your energy." These are different questions.

Eight hours of work on a day when you're running on fumes and constantly distracted is worth less than four focused hours on a high-recovery day. The ultimate optimization target isn't "hours on priority projects" — it's "percentage of time where high-energy windows were matched with high-priority work."

Some days you should push hard. Some days you should clear the admin backlog and go home early. The system tells you which is which, based on your own data and your own historical patterns.

## Weekly Review Philosophy

The weekly review is the highest-ROI 15 minutes of your week. It's structured around one core question: **"Am I spending my time in a way that matches my stated priorities?"**

The review is not about guilt or optimization theater. It's about closing the gap between intention and reality, one small structural change at a time.

Each week, the system suggests **one** concrete change. Not five. One. "This week's biggest lever: protect Tuesday mornings from meetings." You implement it, the system measures whether it worked, and next week you get a new recommendation built on top of the previous one.

Over months, these compound. Week 1: protect morning focus. Week 3: batch Slack to twice daily. Week 6: delegate status updates. Week 10: restructure Thursday to avoid meeting overload. Each change is small. The cumulative effect is transformative.

## Expected Impact

**Tangible wins:**

- Reclaim 5-10 hours per week from invisible time leaks (distractions, rabbit holes, task-transition dead zones)
- Double average deep work block length (from ~25 minutes to ~50-90 minutes)
- Actually delegate work (system removes the friction that prevents it)
- Replace vague end-of-week anxiety with concrete knowledge of what you accomplished

**Behavioral shifts:**

- Stop negotiating with yourself about sleep and exercise once you see the data proving their ROI
- Get better at saying no (you have real data on your capacity)
- Naturally gravitate toward higher-leverage work (seeing "you spent 90 minutes on something Claude could do in 2" is self-correcting)

**Compounding effect:**

- Week 1: reclaim ~2 hours by noticing obvious waste
- Week 4: restructured calendar protecting deep work blocks
- Week 8: morning planning dialed in, matching energy to task priority
- After 2-3 months: ~20-30% more effective without working more hours

**What this won't fix:**

- Wrong priorities (but it will make that very visible very fast)
- Work blocked by other people
- Burnout that needs rest, not optimization
- It's a tool for execution, not strategy

## Guiding Principles

1. **Invisible during work, valuable after work.** The system should never be a distraction itself. Zero friction during the workday. All value delivered in end-of-day and weekly reviews.
    
2. **Evidence over intuition.** Every recommendation is grounded in your own data. "You should protect your mornings" isn't generic advice — it's backed by 4 weeks of data showing your morning deep work blocks are 2.3x longer than afternoon ones.
    
3. **One change at a time.** Don't overwhelm with 10 recommendations. One structural change per week, compounding over months.
    
4. **The system gets smarter, not just bigger.** Every correction, every verified classification, every extracted rule makes the system more accurate and less expensive. By month 2, it should be largely self-running.
    
5. **Privacy first.** All data stays local. No cloud storage of screenshots or window titles. The only external calls are to the Claude API for classification and to integrations you explicitly configure. URL blocklist for sensitive sites. No keystroke logging.
    
6. **Build fast, iterate from real usage.** Get the MVP running in days, not weeks. Live with it. The gaps will reveal themselves faster than you can anticipate them. Don't over-build before you have data.