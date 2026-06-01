"""Website scraping — lightweight by default, ScrapeGraphAI when installed.

Two tools registered:

  • scrape_website(url, prompt)
      Default path:  httpx + BeautifulSoup + the user's main LLM. Pure-Python,
                     VPS-friendly (no playwright, no headless browser).
      Pro path:      If `scrapegraphai` is installed (extra `[scraping-pro]`),
                     uses SmartScraperGraph for JS-heavy sites with Playwright.

  • scrape_to_summary(url)
      Convenience: fetches a page, asks the main LLM to produce a single-
      paragraph plain-English summary. Used by the business-onboarding flow
      to learn the user's company at setup time.

Design constraints:
  • Default install never pulls Playwright — a 200+MB browser bundle on a
    1 GB VPS is unacceptable.
  • All HTTP calls have a 20 s ceiling and follow redirects.
  • HTML is cleaned (nav/script/style/footer stripped) BEFORE the LLM sees
    it, so token spend is bounded.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


# ── Public attempts to use ScrapeGraphAI's SmartScraper if available ────────


def _has_scrapegraphai() -> bool:
    try:
        import scrapegraphai  # noqa: F401
        return True
    except ImportError:
        return False


async def _scrapegraphai_extract(url: str, prompt: str) -> Optional[dict]:
    """Use SmartScraperGraph if installed. Returns None if the lib isn't there
    or any error happens — caller falls back to the lightweight path."""
    try:
        from scrapegraphai.graphs import SmartScraperGraph  # type: ignore
        from argus import config as _config
        cfg = _config.load()
        # Compose the LLM config in the shape SGA expects.
        provider = cfg.agent.default_provider
        model    = cfg.agent.default_model
        info     = None
        from argus.data.providers import get_provider as _gp
        info = _gp(provider)
        api_key = _config.resolve_secret(info.env_var, cfg) or ""
        graph_cfg = {
            "llm": {"model": model, "api_key": api_key, "base_url": info.base_url},
            "verbose": False,
            "headless": True,
        }
        sg = SmartScraperGraph(prompt=prompt, source=url, config=graph_cfg)
        # SGA's .run() is blocking → push to a thread
        result = await asyncio.to_thread(sg.run)
        return result if isinstance(result, dict) else {"result": result}
    except Exception:
        return None


# ── Lightweight path: httpx + BS4-style strip + LLM extraction ──────────────


async def _fetch_and_clean(url: str, *, timeout: float = 20.0) -> tuple[str, str]:
    """Fetch a URL fresh (no cache), return (title, cleaned_text). Strips
    nav/script/style/etc. Returns ("", "ERROR: ...") on failure.

    Real-time: every call hits the network. No cache, no stale data."""
    import httpx
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={
                # Realistic UA so JS-heavy sites don't 403 us as a bot.
                "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/120.0.0.0 Safari/537.36 ARGUS/0.1"),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        ) as c:
            r = await c.get(url)
    except httpx.HTTPError as e:
        return "", f"ERROR fetching {url}: {type(e).__name__}: {e}"

    if r.status_code >= 400:
        return "", f"ERROR: HTTP {r.status_code} from {url}"

    body = r.text or ""
    title = ""
    m = re.search(r"<title[^>]*>([^<]+)</title>", body, re.IGNORECASE)
    if m: title = m.group(1).strip()[:200]

    # Capture meta description + og:description before stripping — they're
    # often the cleanest one-liner about what the page is.
    meta_lines: list[str] = []
    for pat in (
        r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
    ):
        for hit in re.findall(pat, body, re.IGNORECASE):
            meta_lines.append(hit.strip())

    # Strip non-content
    for pat in (r"<script[\s\S]*?</script>", r"<style[\s\S]*?</style>",
                r"<nav[\s\S]*?</nav>", r"<footer[\s\S]*?</footer>",
                r"<header[\s\S]*?</header>", r"<aside[\s\S]*?</aside>",
                r"<noscript[\s\S]*?</noscript>", r"<svg[\s\S]*?</svg>"):
        body = re.sub(pat, "", body, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", body)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#x27;", "'"))
    text = re.sub(r"\s+", " ", text).strip()

    # Prepend meta tags as a "FACTS:" section so the LLM treats them as
    # canonical. Helps a lot on SPAs where the body text is auto-generated junk.
    if meta_lines:
        meta_block = "META TAGS:\n" + "\n".join(f"  - {m}" for m in meta_lines)
        text = meta_block + "\n\nPAGE TEXT:\n" + text
    return title, text[:18_000]   # bumped from 12K so we don't truncate
                                   # the about/products sections of small sites


async def _fetch_multipage(base_url: str, *, max_pages: int = 4,
                            timeout: float = 20.0) -> tuple[str, dict[str, str]]:
    """Fetch the homepage AND up to 3 internal pages (about / products / etc.)
    so the LLM has real grounding on every important section of the site —
    not just one home-page hero.

    Returns (combined_text, page_lengths_dict).
    """
    import httpx
    from urllib.parse import urljoin, urlparse

    # 1. Always start with the homepage
    combined: list[str] = []
    pages_fetched: dict[str, str] = {}

    title, home_text = await _fetch_and_clean(base_url, timeout=timeout)
    if home_text.startswith("ERROR"):
        return home_text, {}
    combined.append(f"=== PAGE: {base_url} (home) ===\n{home_text[:6000]}")
    pages_fetched[base_url] = f"{len(home_text):,} chars"

    # 2. Find internal links worth fetching (about, services, products, pricing).
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 ARGUS/0.1"},
        ) as c:
            r = await c.get(base_url)
        body = r.text or ""
    except Exception:
        return "\n\n".join(combined), pages_fetched

    base_host = urlparse(base_url).netloc
    # Keywords that almost always reveal the business
    target_keywords = ("about", "company", "products", "services", "solutions",
                       "pricing", "team", "what-we-do", "platform", "features")

    href_re = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
    found_links: list[str] = []
    seen: set[str] = {base_url.rstrip("/")}
    for href in href_re.findall(body):
        if any(kw in href.lower() for kw in target_keywords):
            absolute = urljoin(base_url, href)
            # Same-domain only — never wander
            if urlparse(absolute).netloc != base_host:
                continue
            absolute = absolute.split("#")[0].rstrip("/")
            if absolute in seen:
                continue
            seen.add(absolute)
            found_links.append(absolute)
            if len(found_links) >= max_pages - 1:
                break

    # 3. Fetch each in parallel (bounded — sites can rate-limit)
    if found_links:
        results = await asyncio.gather(
            *(_fetch_and_clean(u, timeout=timeout) for u in found_links),
            return_exceptions=True,
        )
        for url, res in zip(found_links, results):
            if isinstance(res, Exception):
                continue
            t, content = res
            if content.startswith("ERROR") or not content:
                continue
            combined.append(f"=== PAGE: {url} ===\n{content[:4000]}")
            pages_fetched[url] = f"{len(content):,} chars"

    return "\n\n".join(combined), pages_fetched


async def _llm_extract(text: str, prompt: str) -> str:
    """Pass the cleaned text + prompt to the user's main LLM. Streams text out.
    Returns the assembled response."""
    from argus import config as _config
    from argus.providers.registry import get_transport
    from argus.providers.base import Message

    cfg = _config.load()
    transport = get_transport(cfg.agent.default_provider, cfg)
    try:
        messages = [
            Message(role="system",
                    content=(
                        "You are a precise web-page reader. The PAGE CONTENT "
                        "below is REAL — you just fetched it. Use ONLY facts "
                        "literally present in that text. If a field is not "
                        "stated, return an empty string or empty list — never "
                        "invent values. Cite verbatim when possible. Output "
                        "only what was asked for; if JSON is requested, "
                        "return ONLY valid JSON."
                    )),
            Message(role="user",
                    content=(
                        f"{prompt}\n\n"
                        f"=== PAGE CONTENT (real, just fetched) ===\n"
                        f"{text[:14_000]}\n"
                        f"=== END PAGE CONTENT ===\n\n"
                        f"Remember: only use facts from the page above."
                    )),
        ]
        out: list[str] = []
        async for evt in transport.stream(
            messages=messages, model=cfg.agent.default_model, tools=[],
        ):
            if hasattr(evt, "text") and evt.text:
                out.append(evt.text)
        return "".join(out).strip()
    finally:
        try:
            await transport.aclose()
        except Exception:
            pass


# ── Tool: scrape_website ────────────────────────────────────────────────────


async def _scrape_website(args: dict[str, Any]) -> str:
    url    = (args.get("url") or "").strip()
    prompt = (args.get("prompt") or "Summarise this page in 3 sentences.").strip()
    if not url:
        return "ERROR: url required"
    if not re.match(r"^https?://", url):
        url = "https://" + url

    # Try SmartScraperGraph first if installed (handles JS), else fall back.
    if _has_scrapegraphai():
        sga_result = await _scrapegraphai_extract(url, prompt)
        if sga_result:
            return json.dumps(sga_result, indent=2)[:6000]

    title, text = await _fetch_and_clean(url)
    if text.startswith("ERROR"):
        return text
    if not text.strip():
        return f"(no readable content extracted from {url})"

    answer = await _llm_extract(text, prompt)
    out = f"# {title or url}\n\n{answer}"
    return out[:6000]


# ── Tool: scrape_to_summary ──────────────────────────────────────────────────


_BUSINESS_PROMPT = """\
You are reading the actual fetched HTML of a company's website (multiple
pages combined). Extract a structured profile as JSON ONLY.

═══ HARD RULES (violating these is a bug) ═══
  1. ONLY use facts that appear in the PAGE TEXT below. Quote verbatim
     when possible. If a field is not stated on the site, use an empty
     string or empty list — DO NOT INVENT.
  2. The "name" must appear literally on the page (title, header, footer,
     or meta tags). If unsure, use the domain name.
  3. "tagline" must be a real one-liner from the site — usually the hero
     headline, meta description, or og:description. If none, return "".
  4. "products" must be items LITERALLY mentioned on the site. Generic
     guesses like "consulting services" are forbidden.
  5. "audience" must be inferrable from the copy ("for developers", "for
     teams", "for ecommerce stores"). Empty string if not stated.

Return JSON in this shape, NOTHING ELSE:

{
  "name":          "<official company name, as it appears on the site>",
  "tagline":       "<verbatim one-line pitch from the site>",
  "what_they_do":  "<2-3 sentence summary built ONLY from page text>",
  "products":      ["<actual product/service name>", ...],
  "audience":      "<who they sell to, as stated on the site>",
  "tone":          "<one word: professional | casual | playful | bold | academic>"
}
"""


async def _scrape_to_summary(args: dict[str, Any]) -> str:
    """Used by setup wizard's business-onboarding step.

    Scrapes the homepage AND up to 3 internal pages (about/products/services
    /pricing) in parallel, then extracts a structured profile from the
    combined real text. Hallucination-resistant: LLM is prompted to use only
    on-page facts and to leave fields empty when unsure.
    """
    url = (args.get("url") or "").strip()
    if not url:
        return '{"_error": "url required"}'
    if not re.match(r"^https?://", url):
        url = "https://" + url

    combined_text, pages = await _fetch_multipage(url, max_pages=4, timeout=20.0)
    if combined_text.startswith("ERROR") or not pages:
        return json.dumps({"_error": combined_text or f"could not fetch {url}",
                           "_source": url})

    answer = await _llm_extract(combined_text, _BUSINESS_PROMPT)
    answer = answer.strip()
    if not answer.startswith("{"):
        m = re.search(r"\{[\s\S]*\}", answer)
        if m: answer = m.group(0)
    try:
        parsed = json.loads(answer)
        parsed["_source"]        = url
        parsed["_pages_fetched"] = pages
        parsed["_total_chars"]   = sum(int(s.split()[0].replace(",", ""))
                                        for s in pages.values()
                                        if s.split()[0].replace(",", "").isdigit())
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        return json.dumps({"_error": "LLM did not return valid JSON",
                           "_raw": answer[:1000],
                           "_source": url,
                           "_pages_fetched": pages})


# ── Registration ────────────────────────────────────────────────────────────


def register_scraping_tools() -> None:
    register(ToolImpl("web", ToolSpec(
        name="scrape_website",
        description=(
            "Fetch a webpage and extract information from it using the main "
            "LLM. Use this for ANY 'what does this site say' or 'extract X "
            "from this URL' question. Handles HTML; falls back to ScrapeGraphAI's "
            "SmartScraperGraph if installed (extra [scraping-pro]) for JS-heavy sites."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url":    {"type": "string", "description": "Full URL (http/https)"},
                "prompt": {"type": "string",
                            "description": "What to extract. e.g. 'List all the team members and their roles' or 'What are the pricing tiers?'"},
            },
            "required": ["url"],
        },
    ), _scrape_website))

    register(ToolImpl("web", ToolSpec(
        name="scrape_to_business_summary",
        description=(
            "Fetch a company's homepage and return a structured JSON summary: "
            "name, tagline, what_they_do, products, audience, tone. Used by "
            "the business-onboarding flow at setup time. Returns JSON only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Company website URL"},
            },
            "required": ["url"],
        },
    ), _scrape_to_summary))
