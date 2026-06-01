---
name: research-brief
category: research
description: "Multi-source research on a topic, deliver a 5-bullet cited brief."
triggers: [research, brief, summary on, what is, give me a primer]
tools: [web_fetch, memory]
author: ARGUS · Fahrenheit Research
version: 1.0.0
---

# Research brief

Fires when the user asks for a primer / brief / summary on a topic.

Steps:
1. Generate 3-5 likely-good source URLs (docs sites, Wikipedia, primary
   sources). Skip listicles and SEO blogspam.
2. `web_fetch` each URL; read the actual content, don't summarise the title.
3. Compile a brief with this exact shape:
   - **TL;DR** (one sentence)
   - **5 key facts** (bullets, each with the source URL inline)
   - **What to read next** (one URL)
4. If the topic has shifted recently (model releases, API changes, news),
   surface the date the cited source was published.

Never invent citations. If you can't fetch a source, say so — don't
pretend you read it.
