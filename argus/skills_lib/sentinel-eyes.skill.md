---
name: sentinel-eyes
category: system
description: "ARGUS the watchman — probe a target endpoint, alert on Telegram if it goes down."
triggers: [watch, monitor, uptime, is the site up, ping]
tools: [web_fetch, memory]
author: ARGUS · Fahrenheit Research
version: 1.0.0
---

# Sentinel eyes — the watchman skill

Named after the namesake (Argus, the hundred-eyed). Fires when the user
asks "is X up?", "watch my site", or "monitor <url>".

Steps:
1. Parse the URL from the request.
2. Call `web_fetch` with the URL and a short timeout (8s).
3. Classify the result:
   - HTTP 2xx → green ("✓ up · status 200 · latency ~280ms")
   - HTTP 3xx redirected → amber ("→ redirect to <new url>")
   - HTTP 4xx / 5xx / network error → red ("✗ down · <reason>")
4. If the user said "watch" or "monitor", record the target into the
   graph with `entity_add` (name=domain, type=monitored_endpoint) so a
   future skill can re-probe it on a schedule.

Don't claim continuous monitoring unless a cron is wired in — be honest
about the single-shot nature in v1.
