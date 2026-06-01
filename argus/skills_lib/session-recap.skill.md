---
name: session-recap
category: memory
description: "End-of-session recap — extract durable facts from this conversation, persist them via memory + graph tools."
triggers: [recap, wrap up, summarize this session, save this]
tools: [memory, graph]
author: ARGUS · Fahrenheit Research
version: 1.0.0
---

# Session recap

Fires at the end of a productive conversation when the user signals
they're done ("wrap up", "save this for later").

Steps:
1. Skim the whole conversation. Identify:
   - Free-form facts the user revealed (preferences, decisions, opinions) → write to `memory.add`.
   - Relational facts (X works at Y, A reports to B) → write via `graph` with `entity_add` + `relation_add`.
2. Group similar facts to avoid clutter; one `memory.add` call per distinct fact.
3. Reply with a short bullet list of what you persisted, so the user can correct anything wrong before it sticks.
4. Skip persistence entirely for transient context (the current bug being debugged, today's weather).

Bias toward fewer, sharper entries. MEMORY.md is bounded at 32 KB —
quality beats volume.
