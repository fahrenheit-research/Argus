---
name: voice-todo
category: memory
description: "Capture a TODO from a Telegram voice memo into MEMORY.md."
triggers: [voice, todo, remind me, note to self]
tools: [memory]
author: ARGUS · Fahrenheit Research
version: 1.0.0
---

# Voice to TODO

Fires when the user dictates something that sounds like a task or
reminder — "remind me to call Sarah tomorrow", "note: ship the
deploy before Friday".

Steps:
1. Extract the actionable verb + object + time qualifier (today, tomorrow, by Friday).
2. Call `memory` with `action="add"` and `content="TODO[<date>] · <task>"`.
3. Confirm with a one-line reply: "Noted: \"<task>\" by <date>."
4. Skip persistence if the input is just conversational ("how's the weather?").

The TODO format `TODO[<date>] · <task>` is what `telegram-daily-digest`
greps for — keep it consistent.
