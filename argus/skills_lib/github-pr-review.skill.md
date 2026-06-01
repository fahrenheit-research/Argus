---
name: github-pr-review
category: coding
description: "Pull a GitHub PR diff, read the changed files, write a focused review."
triggers: [review, pr, pull request, code review]
tools: [web_fetch, memory]
author: ARGUS · Fahrenheit Research
version: 1.0.0
---

# GitHub PR review

Fires when the user pastes a PR URL or asks "review PR #42".

Steps:
1. Parse `<owner>/<repo>#<number>` or the full URL.
2. `web_fetch` `https://github.com/<owner>/<repo>/pull/<n>.diff` for the raw diff.
3. Skim the diff once for *shape* (files touched, scale, obvious wrong patterns).
4. Read the changed files in full via `web_fetch` of the raw GitHub URLs
   — diffs hide context that matters.
5. Write the review in four sections, in order:
   - **Correctness** — bugs, edge cases, race conditions
   - **Naming & readability** — anything that fights the reader
   - **Test coverage** — what's tested, what isn't, where the gap matters
   - **Next steps** — concrete, smallest-possible follow-ups

Keep it terse. No hedging. "Looks great!" is not a review.
