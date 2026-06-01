---
name: telegram-daily-digest
category: communication
description: "Compile a daily digest (open PRs, calendar, memory TODOs) and deliver it to Telegram."
triggers: [daily, digest, summary, end of day, morning brief]
tools: [memory, web_fetch]
author: ARGUS · Fahrenheit Research
version: 1.0.0
---

# Daily digest

Triggered by phrases like "give me my daily digest", "morning brief", or
"end-of-day summary".

Steps:
1. Read MEMORY.md for any open TODOs from the last 7 days.
2. (When the github tool ships) Fetch open PRs assigned to or opened by the user.
3. Compile a markdown summary with three sections:
   - **TODOs** — pinned from MEMORY.md
   - **Open PRs** — title, repo, age, status
   - **Reminders** — anything pinned with a date that falls today
4. Reply in the chat. If invoked from Telegram, the gateway already
   delivers the reply to that chat — no extra send needed.

Style:
- Keep each bullet to one line.
- Surface the count first ("4 open PRs, 2 reminders today, 1 stale TODO").
- Skip empty sections silently.
