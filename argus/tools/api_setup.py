"""Autonomous API onboarding tool — `setup_api_from_docs`.

When the user says "add the Acme API, docs at https://acme.dev/api", the
agent calls this tool which:

  1. Fetches the documentation page
  2. Detects auth scheme (Bearer, API key, OAuth2)
  3. Extracts base URL, key names, env var conventions
  4. Generates clickable links for any token/console pages
  5. Returns a structured setup plan that the agent presents to the user

This is what makes ARGUS autonomous: instead of just storing the URL in
memory, it actually does the integration work.

Hermes pattern: this is a "tool that drives a sub-pipeline" — the same
shape as `delegate_task` and `orchestrate`. The tool itself runs sync
HTTP fetches and pattern matching; the LLM never re-decides during it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


# ── Auth-scheme patterns ──────────────────────────────────────────────────────


_AUTH_PATTERNS = {
    "bearer_token": [
        r"Authorization:\s*Bearer\s+\S",
        r"bearer\s+token",
        r'"Authorization":\s*"Bearer',
    ],
    "api_key_header": [
        r"X-API-Key:",
        r"X-Api-Key:",
        r"api[_-]?key.*header",
        r'"x-api-key":',
    ],
    "api_key_query": [
        r"\?api_key=",
        r"\?apikey=",
        r"\?key=",
    ],
    "basic_auth": [
        r"Authorization:\s*Basic",
        r"HTTP Basic Auth",
    ],
    "oauth2": [
        r"oauth2?[/_-]?token",
        r"client_id.*client_secret",
        r"authorization_code",
        r"refresh_token",
    ],
    "jwt": [
        r"\bJWT\b",
        r"json web token",
    ],
}


_BASE_URL_RE = re.compile(
    r"https?://(?:api\.|sandbox\.|v\d+\.)?[\w.-]+\.[a-z]{2,}(?:/[\w./-]*)?",
    re.IGNORECASE,
)


def _detect_auth_schemes(text: str) -> list[str]:
    """Return all auth schemes mentioned in the docs."""
    detected: list[str] = []
    for scheme, patterns in _AUTH_PATTERNS.items():
        for p in patterns:
            if re.search(p, text, re.IGNORECASE):
                detected.append(scheme)
                break
    return detected


def _extract_base_urls(text: str) -> list[str]:
    """Pull all unique HTTP base URLs from the docs, ranked by API-likelihood.

    Bias heavily toward api.* / v1.* hosts and away from CDN / asset hosts
    (the naïve frequency count surfaces too many image URLs).
    """
    matches = _BASE_URL_RE.findall(text[:15000])    # limit to first 15K chars
    scores: dict[str, int] = {}
    for m in matches:
        clean = m.rstrip("/.,)\"'").split("?")[0].split("#")[0]
        # Skip obvious asset hosts and image extensions
        low = clean.lower()
        if any(s in low for s in ("/static/", "/assets/", "/images/", "/img/",
                                  ".svg", ".png", ".jpg", ".jpeg", ".gif",
                                  ".css", ".js", ".woff", ".ico",
                                  "cdn.", "stripecdn", "fastly", "cloudfront")):
            continue
        score = 1
        if low.startswith("https://api.") or "/api." in low: score += 5
        if "/v1/" in low or "/v2/" in low or "/v3/" in low:  score += 3
        if "sandbox." in low or "staging." in low:            score += 2
        scores[clean] = scores.get(clean, 0) + score
    return [u for u, _ in sorted(scores.items(), key=lambda x: -x[1])[:5]]


def _find_signup_links(text: str) -> list[str]:
    """Find URLs that look like account/console/API-key pages."""
    keywords = ("signup", "sign-up", "register", "api-key", "apikey",
                "console", "dashboard", "developers", "credentials",
                "get-started", "quickstart", "auth")
    candidates = _BASE_URL_RE.findall(text[:20000])
    hits: list[str] = []
    for url in candidates:
        low = url.lower()
        if any(k in low for k in keywords):
            clean = url.rstrip("/.,)\"'")
            if clean not in hits:
                hits.append(clean)
    return hits[:5]


# ── Main tool implementation ─────────────────────────────────────────────────


async def _setup_api(args: dict[str, Any]) -> str:
    """Fetch docs URL, analyse auth, return a structured setup plan."""
    docs_url = (args.get("docs_url") or "").strip()
    name     = (args.get("name") or "").strip() or "the API"
    api_key  = (args.get("api_key") or "").strip()
    base_url_override = (args.get("base_url") or "").strip()
    notes    = (args.get("notes") or "").strip()

    if not docs_url and not base_url_override:
        return (
            "ERROR: need either docs_url (URL of API documentation) "
            "or base_url (the API root). Re-call with at least one."
        )

    # ── Fetch the docs page (if URL given) ─────────────────────────────
    docs_text = ""
    docs_status = ""
    if docs_url:
        try:
            import httpx
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers={"User-Agent": "ARGUS/0.1 API-Onboarder"},
            ) as c:
                r = await c.get(docs_url)
            docs_text = r.text[:30000]
            docs_status = f"HTTP {r.status_code} ({len(docs_text):,} chars)"
        except Exception as e:
            return f"ERROR fetching {docs_url}: {type(e).__name__}: {e}"

    # ── Analyse ────────────────────────────────────────────────────────
    schemes      = _detect_auth_schemes(docs_text) if docs_text else []
    base_urls    = _extract_base_urls(docs_text)   if docs_text else []
    if base_url_override:
        base_urls = [base_url_override] + [u for u in base_urls if u != base_url_override]
    signup_links = _find_signup_links(docs_text)   if docs_text else []

    # ── Build the env var name suggestion ───────────────────────────────
    env_name = (re.sub(r"[^A-Z0-9_]", "_", name.upper().replace(" ", "_"))
                + "_API_KEY").lstrip("_")
    if not env_name or env_name == "_API_KEY":
        env_name = "CUSTOM_API_KEY"

    # ── If user passed an api_key, persist it now ──────────────────────
    persisted = ""
    if api_key:
        try:
            from argus import config as _config
            cfg = _config.load()
            env = dict(cfg.env)
            env[env_name] = api_key
            _config.save_env(env)
            persisted = f"\n✓ Key saved to ~/.argus/.env as {env_name}\n"
        except Exception as e:
            persisted = f"\n⚠ Could not persist key: {e}\n"

    # ── Build the response ─────────────────────────────────────────────
    lines: list[str] = []
    lines.append(f"## API Setup Plan: {name}\n")
    if docs_status:
        lines.append(f"**Docs fetched:** {docs_url} ({docs_status})\n")

    if base_urls:
        lines.append("**Detected base URL(s):**")
        for u in base_urls[:3]:
            lines.append(f"  - {u}")
        lines.append("")

    if schemes:
        lines.append("**Detected auth scheme(s):** " + ", ".join(schemes) + "\n")
    else:
        lines.append("**Auth scheme:** could not auto-detect — read the docs section on authentication\n")

    if signup_links:
        lines.append("**Get your credentials here:**")
        for u in signup_links[:5]:
            lines.append(f"  🔗 {u}")
        lines.append("")

    if persisted:
        lines.append(persisted)
    else:
        lines.append(
            f"**Next steps:**\n"
            f"  1. Get your API key from one of the links above\n"
            f"  2. Call this tool again with `api_key=YOUR_KEY` to save it\n"
            f"     (will store as `{env_name}` in ~/.argus/.env, 0600)\n"
            f"  3. Reference it from any tool/skill as `os.environ['{env_name}']`\n"
        )

    if notes:
        lines.append(f"**Notes:** {notes}\n")

    return "\n".join(lines)


def register_api_setup_tool() -> None:
    register(ToolImpl(
        toolset="memory",
        spec=ToolSpec(
            name="setup_api_from_docs",
            description=(
                "Autonomously onboard a third-party API. Given a documentation URL "
                "(and optionally an API name + key), fetches the docs, detects auth "
                "scheme, extracts base URL, finds signup/credential links, and (if "
                "provided) persists the API key to ~/.argus/.env. Call this when the "
                "user wants to integrate a new service and pastes a docs link."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "docs_url":  {"type": "string",
                                  "description": "URL of the API documentation page to fetch and analyse"},
                    "name":      {"type": "string",
                                  "description": "Friendly name of the API (e.g. 'Acme', 'Stripe')"},
                    "api_key":   {"type": "string",
                                  "description": "OPTIONAL — the actual API key to persist if the user already has one"},
                    "base_url":  {"type": "string",
                                  "description": "OPTIONAL — override the API base URL if docs are ambiguous"},
                    "notes":     {"type": "string",
                                  "description": "OPTIONAL — extra context the user provided"},
                },
                "required": [],
            },
        ),
        handler=_setup_api,
    ))
