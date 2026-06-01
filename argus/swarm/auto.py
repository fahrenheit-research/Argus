"""Automatic swarm detection — routes complex multi-task prompts to the
Constellation without the user having to ask.

ARGUS should feel autonomous: if the user gives a prompt that clearly
contains multiple independent tasks, ARGUS fans out immediately without
waiting to be told "run a constellation."

Detection heuristics (rule-based, zero LLM cost):

  1. MULTI-STEP CONNECTIVES: "and then", "also", "additionally", "as well as",
     "plus", "furthermore", "on top of that", "while you're at it"

  2. COMPOSITE INTENT: research + create + send in one prompt
     (e.g. "research X, write a report about it, and email it to Y")

  3. PARALLEL TASKS: multiple distinct verb-object pairs separated by commas
     or semicolons, each with a different action domain

  4. EXPLICIT MULTI-STEP: "first ... then ... finally", "step 1 ... step 2"

  5. LENGTH + COMPLEXITY: >150 words with >2 distinct action verbs

The classifier returns a float confidence (0.0–1.0). If confidence >= 0.65,
the caller should route to Constellation instead of the normal single-turn loop.

Public API:
    should_swarm(text) -> tuple[bool, float, str]
      bool  = route to swarm?
      float = confidence (0.0–1.0)
      str   = reason (one sentence, for the UI status line)
"""

from __future__ import annotations

import re

# ── Connective patterns (strong signal) ─────────────────────────────────────

_MULTI_CONNECTIVES = [
    r"\band then\b", r"\balso\b", r"\badditionally\b", r"\bas well as\b",
    r"\bplus\b", r"\bfurthermore\b", r"\bon top of that\b",
    r"\bwhile you(?:'re| are) at it\b", r"\bat the same time\b",
    r"\bsimultaneously\b", r"\bin addition\b", r"\bmoreover\b",
    r"\bthen\b",   # only counts once, below
]

# ── Action-domain vocabulary (for composite-intent detection) ────────────────

_RESEARCH_VERBS   = {"research", "find", "search", "look up", "investigate",
                      "analyse", "analyze", "check", "gather", "collect",
                      "explore", "compare", "benchmark", "survey", "review",
                      "scan", "scrape", "fetch", "get", "discover"}

_CREATE_VERBS     = {"write", "create", "draft", "generate", "produce", "make",
                      "build", "author", "compose", "prepare", "design",
                      "document", "summarise", "summarize", "format", "compile",
                      "put together", "structure", "organize", "arrange"}

_COMMUNICATE_VERBS = {"send", "email", "share", "forward", "post", "publish",
                       "notify", "alert", "message", "dm", "tweet", "schedule",
                       "deliver", "distribute", "broadcast"}

_EXEC_VERBS        = {"deploy", "run", "execute", "launch", "start", "trigger",
                       "automate", "process", "apply", "install", "setup",
                       "configure", "activate", "integrate"}

_DOMAIN_GROUPS = [_RESEARCH_VERBS, _CREATE_VERBS, _COMMUNICATE_VERBS, _EXEC_VERBS]


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"\b[a-z]+\b", text.lower()))


def _count_connectives(text: str) -> int:
    low = text.lower()
    count = 0
    seen_then = False
    for pat in _MULTI_CONNECTIVES:
        hits = len(re.findall(pat, low))
        if pat == r"\bthen\b":
            # "then" alone is weak — count only once and only if >= 1
            if hits and not seen_then:
                count += 1
                seen_then = True
        else:
            count += min(hits, 1)   # cap each connective at 1
    return count


def _count_domain_hits(text: str) -> int:
    """How many distinct action domains (research / create / communicate / exec)
    appear in the text? 2+ = composite intent."""
    words = _word_set(text)
    hit_count = 0
    for domain in _DOMAIN_GROUPS:
        if words & domain:
            hit_count += 1
    return hit_count


def _count_task_segments(text: str) -> int:
    """Approximate number of distinct task segments by counting strong list
    separators (comma + verb, semicolon, numbered list items)."""
    # Numbered list: "1. do X, 2. do Y" or "first ... then ..."
    num_list = len(re.findall(r"(?:^|\n)\s*\d+\.\s+\w+", text, re.MULTILINE))
    # Semicolons separating clauses
    semicolons = len(re.findall(r";\s+\w", text))
    # "first..., then..., finally..."
    ordinals = len(re.findall(
        r"\b(?:first|second|third|next|then|finally|lastly|after that)\b",
        text.lower()
    ))
    return num_list + semicolons + min(ordinals, 3)


def should_swarm(text: str) -> tuple[bool, float, str]:
    """Decide whether this prompt should be routed to the Constellation swarm.

    Returns (route_to_swarm, confidence, reason_string).

    Calibration targets:
      - "What's the weather?" → confidence ~0.0 → no swarm
      - "Research X" → ~0.1 → no swarm
      - "Research X and write a report" → ~0.55 → borderline (no swarm)
      - "Research X, write a report, and email it to Y" → ~0.75 → SWARM
      - "Find the top 5 VCs in AI, draft outreach emails, add to CRM, send to boss" → ~0.92 → SWARM
    """
    if not text or len(text.strip()) < 15:
        return False, 0.0, "too short to evaluate"

    low  = text.lower()
    words = re.findall(r"\b\w+\b", text)
    word_count = len(words)

    score = 0.0
    reasons: list[str] = []

    # ── Signal 1: connectives (+0.15 each, max +0.45) ───────────────────
    n_conn = _count_connectives(text)
    if n_conn >= 1:
        delta = min(n_conn * 0.18, 0.45)
        score += delta
        reasons.append(f"{n_conn} multi-task connective(s)")

    # ── Signal 2: cross-domain composite intent ──────────────────────────────
    # Two different domains in one prompt (research + create, create + send, etc.)
    # is the clearest signal of parallel work. Weight heavily.
    n_domains = _count_domain_hits(text)
    if n_domains >= 2:
        delta = (n_domains - 1) * 0.35   # 2 domains=+0.35, 3=+0.70
        score += delta
        reasons.append(f"{n_domains} action domains (research/create/send/exec)")

    # ── Signal 2b: "and" joining two different action verbs ───────────────
    # "research X and write a report" — no connective counted above, but the
    # pattern is clearly multi-step. Detect "verb ... and ... verb" where the
    # two verbs belong to different domains.
    if n_domains >= 2:
        and_bridge = re.search(
            r"\b(?:" + "|".join(sorted(_RESEARCH_VERBS | _CREATE_VERBS)) + r")\b"
            r".{0,60}\band\b.{0,60}"
            r"\b(?:" + "|".join(sorted(_CREATE_VERBS | _COMMUNICATE_VERBS | _EXEC_VERBS)) + r")\b",
            low,
        )
        if and_bridge:
            score += 0.18
            reasons.append("'and' bridges two different action verbs")

    # ── Signal 3: task segments (numbered list, semicolons, ordinals) ────
    n_segs = _count_task_segments(text)
    if n_segs >= 2:
        score += min(n_segs * 0.12, 0.30)
        reasons.append(f"{n_segs} explicit task segments")

    # ── Signal 4: long complex prompt (>100 words + multiple actions) ────
    if word_count > 100 and n_domains >= 2:
        score += 0.10
        reasons.append(f"complex prompt ({word_count} words)")

    # ── Signal 5: hard-trigger phrases (explicit parallel work) ──────────
    hard_triggers = [
        r"for each\s+\w+",          # "for each client, do X"
        r"across\s+all\s+\w+",      # "across all platforms"
        r"(\d+)\s+(?:different|separate|distinct)\s+\w+",  # "5 different reports"
        r"in parallel\b",
        r"at the same time\b",
    ]
    for pat in hard_triggers:
        if re.search(pat, low):
            score += 0.15
            reasons.append(f"hard-trigger: '{pat[:30]}'")
            break   # cap at one hard trigger bonus

    # ── Cap and threshold ─────────────────────────────────────────────────
    score = min(score, 1.0)
    THRESHOLD = 0.50   # lower threshold so multi-domain prompts auto-swarm
    routable  = score >= THRESHOLD
    reason    = (", ".join(reasons[:3]) or "single-task prompt") + \
                f" (confidence {score:.0%})"

    return routable, score, reason
