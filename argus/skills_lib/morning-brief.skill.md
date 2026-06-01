---
name: morning-brief
category: communication
description: "First-message-of-the-day briefing: TODOs from MEMORY, recent decisions, top entity from the graph."
triggers: [good morning, brief me, what did i miss, where were we]
tools: [memory, graph]
author: ARGUS · Fahrenheit Research
version: 1.0.0
---

# Morning brief

Fires on the first message of the day or when the user explicitly asks
"where were we?" / "brief me".

Steps:
1. Read MEMORY.md, scan for entries from the past 7 days.
2. `graph` recall the top 5 entities (likely current projects/people).
3. Surface, in order:
   - **Open TODOs** (anything matching `TODO[<date>]` from voice-todo)
   - **Recent decisions** (entries that contain "decided" or "picked")
   - **Active context** (top 3 entities from the graph)
4. End with one question to set today's focus ("what's the first thing
   you want to push on?").

Skip if MEMORY.md is empty — just greet the user briefly.
