"""Business profile — the company ARGUS works for.

Persisted at `~/.argus/business.json`. Captured during setup by scraping
the user's company website, then re-used at every CLI boot to render the
personalised welcome panel.

Schema (versioned so future migrations don't lose data):

  {
    "_version": 1,
    "url": "https://he2.ai",
    "scraped_at": 1717250000,
    "name": "He2.ai",
    "tagline": "...",
    "what_they_do": "...",
    "products": ["..."],
    "audience": "...",
    "tone": "professional",
    "suggestions": ["...", "...", "..."],
    "suggestions_generated_at": 1717250000
  }

Suggestions are cached for 24h to avoid burning tokens on every boot.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from argus import paths


_BUSINESS_FILE = paths.HOME / "business.json"
_SUGGESTION_TTL_SECS = 24 * 3600


# ── First-run detection ─────────────────────────────────────────────────────


def is_first_run(profile: Optional[dict[str, Any]] = None) -> bool:
    """True if the business profile exists but the user has never seen the
    full intelligence briefing. Used by chat.run() to decide between the
    rich first-time experience and the subtle daily welcome.
    """
    p = profile if profile is not None else load()
    if not p:
        return False
    return not p.get("first_run_completed", False)


def reset_for_re_onboarding() -> None:
    """Drop the first-run / chosen-use-case / suggestion cache so the next
    CLI boot treats THIS profile as a fresh first-run. Called whenever the
    user re-points ARGUS at a different business (or refreshes the same
    one) — they get the full intelligence briefing again with the new data.

    Does NOT touch the scraped profile fields themselves — only the
    "I've been here before" markers.
    """
    p = load() or {}
    for key in ("first_run_completed", "first_run_at",
                "chosen_use_case", "chosen_use_case_at",
                "suggestions", "suggestions_generated_at"):
        p.pop(key, None)
    save(p)


def mark_first_run_done(use_case_selected: Optional[dict[str, Any]] = None) -> None:
    """Flip the first-run flag so subsequent boots get the quieter welcome.
    Optionally record which use case the user picked so future sessions can
    follow up on it."""
    profile = load() or {}
    profile["first_run_completed"] = True
    profile["first_run_at"] = int(time.time())
    if use_case_selected:
        profile["chosen_use_case"]    = use_case_selected
        profile["chosen_use_case_at"] = int(time.time())
    save(profile)


# ── Persistence ──────────────────────────────────────────────────────────────


def load() -> Optional[dict[str, Any]]:
    """Return the saved business profile, or None if not configured."""
    if not _BUSINESS_FILE.exists():
        return None
    try:
        return json.loads(_BUSINESS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save(profile: dict[str, Any]) -> Path:
    paths.ensure_dirs()
    profile.setdefault("_version", 1)
    _BUSINESS_FILE.write_text(json.dumps(profile, indent=2))
    return _BUSINESS_FILE


def clear() -> bool:
    if _BUSINESS_FILE.exists():
        _BUSINESS_FILE.unlink()
        return True
    return False


# ── Scrape + persist (called by setup wizard) ────────────────────────────────


async def onboard_from_url(url: str) -> tuple[bool, dict[str, Any] | str]:
    """Scrape `url`, parse the JSON the LLM returns, save to disk.

    Returns (ok, profile_dict) on success, or (False, error_string) on failure.
    """
    from argus.tools.scraping import _scrape_to_summary
    raw = await _scrape_to_summary({"url": url})
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False, f"scrape returned non-JSON: {raw[:300]}"
    if "_error" in parsed:
        return False, parsed["_error"]
    profile = {
        "_version": 1,
        "url":          parsed.get("_source", url),
        "scraped_at":   int(time.time()),
        "name":         parsed.get("name", "") or _hostname(url),
        "tagline":      parsed.get("tagline", ""),
        "what_they_do": parsed.get("what_they_do", ""),
        "products":     parsed.get("products", []) or [],
        "audience":     parsed.get("audience", ""),
        "tone":         parsed.get("tone", "professional"),
    }
    save(profile)
    return True, profile


def _hostname(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return (m.group(1) if m else url).replace("www.", "")


# ── Daily personalised suggestions (cached 24h) ──────────────────────────────


async def get_or_refresh_suggestions(profile: dict[str, Any]) -> list[str]:
    """Return 3 personalised suggestions for the user. Cached for 24h.

    Cheap on average — only a real LLM call once per day per machine.
    """
    cached = profile.get("suggestions") or []
    cached_at = profile.get("suggestions_generated_at") or 0
    if cached and (time.time() - cached_at) < _SUGGESTION_TTL_SECS:
        return cached[:3]

    try:
        fresh = await _generate_suggestions(profile)
    except Exception:
        # Network down? Fall back to anything cached, even if stale.
        return cached[:3] if cached else []
    if fresh:
        profile["suggestions"] = fresh[:3]
        profile["suggestions_generated_at"] = int(time.time())
        save(profile)
    return fresh[:3]


async def generate_first_run_use_cases(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate three rich, executable use cases for the first-run briefing.

    Different from `_generate_suggestions` (which produces 1-line daily nudges).
    These are deep proposals the user picks from to kick off their first session.

    Returns a list of dicts:
      [{title, why, first_step, effort, category}, ...]

    Falls back to safe placeholders if the LLM call fails so the first-run
    flow never blocks the user.
    """
    from argus import config as _config
    from argus.providers.registry import get_transport
    from argus.providers.base import Message

    cfg = _config.load()
    prompt = (
        f"You are ARGUS, an AI agent that works for ONE business and can act "
        f"autonomously using a real toolkit: web_search, web_extract, web_fetch, "
        f"scrape_website, gmail_send, gmail_search, vision_analyze, execute_code, "
        f"calculator, get_datetime, memory, todo, constellation (multi-agent "
        f"research), text_to_speech.\n\n"
        f"BUSINESS PROFILE:\n"
        f"  Name:         {profile.get('name', '?')}\n"
        f"  URL:          {profile.get('url', '?')}\n"
        f"  Tagline:      {profile.get('tagline', '?')}\n"
        f"  What they do: {profile.get('what_they_do', '?')}\n"
        f"  Products:     {', '.join(profile.get('products', []))}\n"
        f"  Audience:     {profile.get('audience', '?')}\n\n"
        f"Propose THREE distinct, high-impact use cases you could execute "
        f"RIGHT NOW for this business. Each must be:\n"
        f"  • SPECIFIC — name the deliverable, not the activity. "
        f"    (bad: 'help with marketing' / good: 'draft 5 cold-email "
        f"    variants for your top 10 ideal-customer accounts')\n"
        f"  • REAL — you must list which of YOUR tools you'll call first.\n"
        f"  • IMPRESSIVE — pick things that make the user say 'oh damn'.\n\n"
        f"Return JSON only, no prose:\n\n"
        f"[\n"
        f"  {{\n"
        f"    \"title\":      \"<imperative verb-led headline, max 60 chars>\",\n"
        f"    \"why\":        \"<one sentence — why this matters for THEM specifically>\",\n"
        f"    \"first_step\": \"<exact first tool call, e.g. scrape_website(url='...')>\",\n"
        f"    \"effort\":     \"<like: '2 min' | '5-10 min' | '~30 min'>\",\n"
        f"    \"category\":   \"<one of: research | content | outreach | analysis | automation>\"\n"
        f"  }},\n"
        f"  ...\n"
        f"]"
    )
    transport = get_transport(cfg.agent.default_provider, cfg)
    try:
        out: list[str] = []
        async for evt in transport.stream(
            messages=[
                Message(role="system",
                        content="You are a sharp business strategist. Output valid JSON arrays only."),
                Message(role="user", content=prompt),
            ],
            model=cfg.agent.default_model, tools=[],
        ):
            if hasattr(evt, "text") and evt.text:
                out.append(evt.text)
        text = "".join(out).strip()
        m = re.search(r"\[[\s\S]*\]", text)
        if not m:
            return _fallback_use_cases(profile)
        items = json.loads(m.group(0))
        # Validate + normalise
        clean: list[dict[str, Any]] = []
        for it in items[:3]:
            if not isinstance(it, dict):
                continue
            clean.append({
                "title":      str(it.get("title", "")).strip()[:80] or "(no title)",
                "why":        str(it.get("why", "")).strip()[:240],
                "first_step": str(it.get("first_step", "")).strip()[:240],
                "effort":     str(it.get("effort", "?")).strip()[:24],
                "category":   str(it.get("category", "automation")).strip()[:24],
            })
        return clean if len(clean) == 3 else _fallback_use_cases(profile)
    except Exception:
        return _fallback_use_cases(profile)
    finally:
        try: await transport.aclose()
        except Exception: pass


def _fallback_use_cases(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Safe defaults that work for any business — used when the LLM call fails."""
    name = profile.get("name", "your business")
    return [
        {
            "title":      f"Run a deep competitive scan around {name}",
            "why":        "Surface 3-5 nearest competitors + how they position differently.",
            "first_step": f"constellation(goal='Find the 5 closest competitors to {name} and how each positions differently', budget_tokens=20000)",
            "effort":     "5-8 min",
            "category":   "research",
        },
        {
            "title":      "Audit your homepage for clarity + conversion gaps",
            "why":        "Catch the friction-points before your next visitor does.",
            "first_step": f"scrape_website(url='{profile.get('url', '')}', prompt='List every CTA, friction-point, and unclear copy line.')",
            "effort":     "2-3 min",
            "category":   "analysis",
        },
        {
            "title":      "Draft 3 outbound email variants for your ICP",
            "why":        "Test subject-line + opener variations against your real positioning.",
            "first_step": "Use your business profile to draft 3 cold-email variants with different angles (pain, curiosity, social-proof).",
            "effort":     "3-5 min",
            "category":   "outreach",
        },
    ]


def run_me_wizard(console: Any) -> bool:
    """Interactive `argus me` flow — paste a URL, scrape it fresh, save the
    profile, reset first-run flags so the next CLI boot opens with the
    rich briefing on the new data.

    Shared by:
      - `argus me [url]`             (cli.py)
      - "argus me" typed in chat     (screens/chat.py natural-language trigger)
      - `/me` slash command          (slash.py)

    Returns True if a new profile was saved, False if the user skipped.
    """
    import asyncio as _aio
    from argus import picker as _picker
    from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.text import Text

    BEIGE = "#F5E6C8"

    console.print()
    console.print(Text("  ⟨◇⟩  ", style=GOLD)
                  .append("LEARN MY BUSINESS", style=f"bold {MAGENTA}")
                  .append("   ·   ", style=DIM)
                  .append("paste a URL · I'll scan it", style=DIM))
    console.print(Text("  " + "─" * 70, style=DIM))

    existing = load()
    if existing:
        name = existing.get("name") or existing.get("url") or "your business"
        info = Text()
        info.append("  Currently watching: ", style=DIM)
        info.append(name, style=f"bold {GOLD}")
        if existing.get("url"):
            info.append(f"  ({existing['url']})", style=CYAN)
        console.print(info)
        console.print()
        action = _picker.select(
            "what now",
            choices=[
                _picker.Choice(value="new",     label="Paste a new URL",
                                description="forget the old profile, scan fresh"),
                _picker.Choice(value="refresh", label="Re-scan the same URL",
                                description=f"refetch {existing.get('url', '')}"),
                _picker.Choice(value="cancel",  label="Cancel — keep what I had",
                                description="no changes"),
            ],
            default="new",
        )
        if not action or action == "cancel":
            console.print(Text("  Kept the existing profile.", style=DIM))
            return False
        if action == "refresh":
            url = existing.get("url", "").strip()
            if not url:
                console.print(Text("  ✗ no URL on file — switching to new-URL mode.", style=DIM))
                action = "new"
        if action == "new":
            url = _picker.text("Business website URL:")
    else:
        url = _picker.text("Business website URL:")

    if not url or not url.strip():
        console.print(Text("  Skipped — no URL entered.", style=DIM))
        return False
    url = url.strip()

    # ── Scan animation ──────────────────────────────────────────────
    console.print()
    console.print(Text("  ⟨◇⟩  Scanning ", style=GOLD)
                  .append(url, style=CYAN)
                  .append("  (≤30s — multi-page fetch)", style=DIM))

    try:
        ok, result = _aio.run(onboard_from_url(url))
    except Exception as e:  # noqa: BLE001
        console.print(Text(f"  ✗ scrape crashed: {type(e).__name__}: {e}", style="red"))
        return False

    if not ok:
        console.print(Text(f"  ✗ {result}", style="red"))
        return False

    # ── Reset the first-run flag so next boot shows the new briefing ─
    reset_for_re_onboarding()

    # ── Show summary panel ──────────────────────────────────────────
    profile = result
    body = Text()
    body.append("  ✓ ", style=GOLD)
    body.append("Learned ", style=DIM)
    body.append(profile.get("name", "the business"), style=f"bold {GOLD}")
    body.append("\n")
    if profile.get("tagline"):
        body.append("    ", style=DIM)
        body.append(f'"{profile["tagline"]}"', style=f"italic {BEIGE}")
        body.append("\n")
    if profile.get("what_they_do"):
        body.append("\n    ", style=DIM)
        body.append(profile["what_they_do"][:320], style=BEIGE)
        body.append("\n")
    if profile.get("products"):
        body.append("\n    Products: ", style=DIM)
        body.append(", ".join(profile["products"][:6]), style=BEIGE)
        body.append("\n")
    if profile.get("audience"):
        body.append("    Audience: ", style=DIM)
        body.append(profile["audience"][:80], style=BEIGE)
        body.append("\n")
    # Show evidence of what we fetched
    pages = profile.get("_pages_fetched", {})
    if pages:
        body.append(f"\n    Fetched ", style=DIM)
        body.append(f"{len(pages)} page(s)", style=CYAN)
        body.append(", ", style=DIM)
        body.append(f"{profile.get('_total_chars', '?'):,} chars", style=CYAN)
        body.append(" of real content.", style=DIM)

    console.print(Padding(Panel(
        body, border_style=MAGENTA, padding=(0, 2),
        title=f"⟨◇⟩  {profile.get('name', 'business')}",
        title_align="left",
    ), (1, 0)))

    console.print(Text("  Next ", style=DIM)
                  .append("argus", style=CYAN)
                  .append(" boot will open with a fresh intelligence briefing on the new profile.",
                          style=DIM))
    return True


async def _generate_suggestions(profile: dict[str, Any]) -> list[str]:
    """Ask the user's main LLM for 3 short, concrete actions ARGUS can take
    for this business right now."""
    from argus import config as _config
    from argus.providers.registry import get_transport

    cfg = _config.load()
    prompt = (
        f"You are ARGUS, an AI agent that works for one specific business.\n\n"
        f"Business name:  {profile.get('name', 'the user')}\n"
        f"What they do:   {profile.get('what_they_do', '(not provided)')}\n"
        f"Products:       {', '.join(profile.get('products', []) or [])}\n"
        f"Audience:       {profile.get('audience', '(not provided)')}\n\n"
        f"Suggest THREE concrete actions you (ARGUS) could take RIGHT NOW "
        f"that would meaningfully help this business today. Each suggestion "
        f"must be ONE line, action-verb first, under 70 characters.\n\n"
        f"Return as a JSON array of 3 strings. Nothing else."
    )
    from argus.providers.base import Message
    transport = get_transport(cfg.agent.default_provider, cfg)
    try:
        out: list[str] = []
        async for evt in transport.stream(
            messages=[
                Message(role="system",
                        content="You are a precise business strategist who outputs valid JSON only."),
                Message(role="user", content=prompt),
            ],
            model=cfg.agent.default_model,
            tools=[],
        ):
            if hasattr(evt, "text") and evt.text:
                out.append(evt.text)
        text = "".join(out).strip()
        # Extract first JSON array
        m = re.search(r"\[[\s\S]*?\]", text)
        if not m:
            return []
        items = json.loads(m.group(0))
        return [str(s).strip().rstrip(".")[:80]
                for s in items if isinstance(s, (str, int, float))][:3]
    finally:
        try: await transport.aclose()
        except Exception: pass
