"""Faux LLM provider that streams a canned reply token-by-token.

When real ProviderTransport classes land they'll implement the same
`stream(prompt) -> Iterator[str]` interface — every UI line that touches
streaming is unchanged.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Iterator

_CANNED_REPLIES: list[str] = [
    "Sure — here is what I'd do, step by step:\n\n"
    "1. Open the PR locally with `gh pr checkout <number>` so you have the full tree.\n"
    "2. Skim the diff once at a high level, looking for obvious shape changes.\n"
    "3. Read the *changed files in full* — not just the diff — to catch context loss.\n"
    "4. Run the test suite once before reading the new tests to see what fails today.\n"
    "5. Write up correctness, naming, edge cases, and test-coverage notes in that order.\n\n"
    "Want me to draft a review comment for the current PR you're looking at?",

    "Quick read:\n\n"
    "- The change is **mostly mechanical** — replacing the legacy `format_user` "
    "calls with the new `Formatter` protocol.\n"
    "- One subtle bug: `Formatter.render()` returns `str | None`, and three callers "
    "pass the result straight into `len()`. That will raise on the empty path.\n"
    "- The new test only covers the happy path. Add a parametrised case for the "
    "`None` return so the regression sticks.\n\n"
    "Net: ship it after the `len()` fix.",

    "Plan:\n\n"
    "1. **Investigate** — what does the gateway log say at the moment the connection drops?\n"
    "2. **Reproduce locally** with `argus gateway start --service=false --debug`.\n"
    "3. **Bisect** between the two commits that bracket the regression.\n"
    "4. **Patch + test** — add a unit test that triggers the failing code path.\n"
    "5. **Ship** behind `gateway.telegram.experimental_reconnect = true` until "
    "we have 48h of clean logs.\n\n"
    "I can start at step 1 — want me to tail the last hour of gateway logs?",

    "Three options, in increasing order of effort:\n\n"
    "- **Easy.** Bump the `edit_throttle_ms` from 600 to 900. Costs you a "
    "perceptible streaming delay; gains you headroom against Telegram's floodlimit.\n"
    "- **Medium.** Coalesce trailing token edits into a single final edit. Keeps "
    "the perceived speed, eliminates ~40% of edits.\n"
    "- **Hard.** Switch the gateway from long-polling to webhooks. Faster, "
    "cheaper, but introduces an inbound HTTPS dependency.\n\n"
    "Default to medium for v1.0. Hard is a v0.4 conversation.",
]


class FauxProvider:
    """Pretends to be a streaming LLM. Yields tokens with realistic jitter."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def stream(self, prompt: str) -> Iterator[str]:
        reply = self._pick_reply(prompt)
        for tok in self._tokenise(reply):
            time.sleep(self._rng.uniform(0.012, 0.045))
            yield tok

    def _pick_reply(self, prompt: str) -> str:
        return self._rng.choice(_CANNED_REPLIES)

    @staticmethod
    def _tokenise(text: str) -> list[str]:
        # Split on word/punct boundaries while keeping whitespace attached to
        # the following token, mimicking a real subword stream closely enough.
        parts = re.findall(r"\s+\S+|\S+", text)
        # Break a few "long" tokens further so the stream feels grainy.
        out: list[str] = []
        for p in parts:
            if len(p) > 8 and " " not in p:
                mid = len(p) // 2
                out.append(p[:mid])
                out.append(p[mid:])
            else:
                out.append(p)
        return out
