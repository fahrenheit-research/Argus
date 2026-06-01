"""Research engine — purpose-built no-hallucination web tools for ARGUS.

Three tools registered, ranked by quality:

  • web_search      → real-time web search with cited snippets
  • web_research    → deep multi-source research with structured answer
  • web_fetch_clean → fetch one URL and return clean Markdown

Backend chain (auto-picked by which API key the user has):

    1. Tavily    — purpose-built for LLM agents; cleanest snippets +
                   citations; "advanced" mode pulls full page content.
                   `TAVILY_API_KEY`   (free tier: 1k searches/month)
    2. Exa       — neural search; great for "find me sites like X" queries.
                   `EXA_API_KEY`      (free tier: 1k searches/month)
    3. Perplexity — answer-first API; returns synthesised answer + sources.
                   `PERPLEXITY_API_KEY`
    4. Brave     — privacy-first general search.
                   `BRAVE_SEARCH_API_KEY`
    5. SerpAPI   — Google results.
                   `SERPAPI_KEY`
    6. DuckDuckGo — no-key fallback so the tool ALWAYS works.

ANTI-HALLUCINATION CONTRACT (enforced in the system prompt):

  - Every claim made by the agent in response to a web_research call MUST
    be traceable to one of the URLs we return.
  - We return RAW snippets with their source URLs, not synthesized text.
    The agent does the synthesis but is explicitly forbidden from
    inventing facts not in the snippets.
  - If the search returns no hits, we say so plainly. The agent then
    tells the user — never fills the gap with plausible-but-fake info.

Set keys via:  argus key tavily tvly-xxxxx
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


# ── Result types ────────────────────────────────────────────────────────────


@dataclass
class SearchHit:
    """One search result. NEVER synthesised — `snippet` is raw text from
    the source, `url` is the page it came from."""
    title:   str
    url:     str
    snippet: str
    score:   float = 0.0           # backend's relevance score (0-1) if any
    backend: str   = ""            # which provider returned it (for citations)


# ── Backend detection ───────────────────────────────────────────────────────


def _key(name: str) -> str:
    """Resolve an API key from env or ~/.argus/.env."""
    from argus import config as _cfg
    return (os.environ.get(name)
            or _cfg.resolve_secret(name)
            or "")


def _available_backend() -> str:
    """Return the best available backend by checking which key is set.
    Falls back to 'duckduckgo' which needs no key."""
    if _key("TAVILY_API_KEY"):      return "tavily"
    if _key("EXA_API_KEY"):         return "exa"
    if _key("PERPLEXITY_API_KEY"):  return "perplexity"
    if _key("BRAVE_SEARCH_API_KEY"):return "brave"
    if _key("SERPAPI_KEY"):         return "serpapi"
    return "duckduckgo"


def _backend_label(b: str) -> str:
    return {
        "tavily":      "Tavily (advanced mode, LLM-tuned)",
        "exa":         "Exa (neural search)",
        "perplexity":  "Perplexity (sonar-pro)",
        "brave":       "Brave Search",
        "serpapi":     "Google via SerpAPI",
        "duckduckgo":  "DuckDuckGo (no-key fallback)",
    }.get(b, b)


# ── Backend implementations ─────────────────────────────────────────────────


async def _search_tavily(query: str, limit: int) -> list[SearchHit]:
    """Tavily — best for LLM agents. Returns clean snippets with scores."""
    key = _key("TAVILY_API_KEY")
    if not key:
        return []
    payload = {
        "api_key": key,
        "query": query,
        "search_depth": "advanced",          # fetches full page content, not just summaries
        "max_results": max(1, min(limit, 10)),
        "include_answer": False,             # we want raw hits, not synthesised answers
        "include_raw_content": True,         # FULL page text — anti-hallucination grounding
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.tavily.com/search", json=payload)
    if r.status_code >= 400:
        return []
    data = r.json()
    hits: list[SearchHit] = []
    for item in data.get("results", [])[:limit]:
        # Prefer raw_content (full page) over content (snippet) for grounding
        snippet = item.get("raw_content") or item.get("content") or ""
        hits.append(SearchHit(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=snippet[:1500],
            score=float(item.get("score", 0.0)),
            backend="tavily",
        ))
    return hits


async def _search_exa(query: str, limit: int) -> list[SearchHit]:
    """Exa — neural search engine. Excellent for semantic queries."""
    key = _key("EXA_API_KEY")
    if not key:
        return []
    payload = {
        "query": query,
        "numResults": max(1, min(limit, 10)),
        "contents": {"text": {"maxCharacters": 1500}, "highlights": {"numSentences": 3}},
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": key, "Content-Type": "application/json"},
            json=payload,
        )
    if r.status_code >= 400:
        return []
    data = r.json()
    hits: list[SearchHit] = []
    for item in data.get("results", [])[:limit]:
        text = (item.get("text", "")
                or " ".join(item.get("highlights", []))
                or item.get("title", ""))
        hits.append(SearchHit(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=text[:1500],
            score=float(item.get("score", 0.0)),
            backend="exa",
        ))
    return hits


async def _search_perplexity(query: str, limit: int) -> list[SearchHit]:
    """Perplexity — sonar-pro model returns synthesised answer + sources.
    We use ONLY the sources, not the synthesised text, to keep our
    anti-hallucination contract intact."""
    key = _key("PERPLEXITY_API_KEY")
    if not key:
        return []
    payload = {
        "model": "sonar-pro",
        "messages": [{"role": "user", "content": query}],
        "return_citations": True,
        "return_related_questions": False,
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json=payload,
        )
    if r.status_code >= 400:
        return []
    data = r.json()
    # Perplexity returns the answer in choices[0].message.content with
    # citations in a separate `citations` field. Extract URLs only.
    citations = data.get("citations", []) or []
    hits: list[SearchHit] = []
    for i, url in enumerate(citations[:limit]):
        hits.append(SearchHit(
            title=f"(perplexity citation #{i + 1})",
            url=url,
            snippet="",       # we'll fetch real text below if user wants it
            score=1.0 - (i / max(1, len(citations))),
            backend="perplexity",
        ))
    return hits


async def _search_brave(query: str, limit: int) -> list[SearchHit]:
    """Brave Search API — privacy-first, no-tracking, clean results."""
    key = _key("BRAVE_SEARCH_API_KEY")
    if not key:
        return []
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": key,
                     "Accept": "application/json"},
            params={"q": query, "count": max(1, min(limit, 20))},
        )
    if r.status_code >= 400:
        return []
    data = r.json()
    hits: list[SearchHit] = []
    for item in data.get("web", {}).get("results", [])[:limit]:
        hits.append(SearchHit(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("description", "")[:1500],
            score=0.0,
            backend="brave",
        ))
    return hits


async def _search_serpapi(query: str, limit: int) -> list[SearchHit]:
    """SerpAPI — Google results."""
    key = _key("SERPAPI_KEY")
    if not key:
        return []
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            "https://serpapi.com/search",
            params={"q": query, "api_key": key, "num": min(limit, 10)},
        )
    if r.status_code >= 400:
        return []
    data = r.json()
    hits: list[SearchHit] = []
    for item in data.get("organic_results", [])[:limit]:
        hits.append(SearchHit(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", "")[:1500],
            score=0.0,
            backend="serpapi",
        ))
    return hits


async def _search_duckduckgo(query: str, limit: int) -> list[SearchHit]:
    """DuckDuckGo — no API key required. The safety net."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS   # type: ignore
    except ImportError:
        return []

    def _run() -> list[SearchHit]:
        hits: list[SearchHit] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=limit):
                hits.append(SearchHit(
                    title=item.get("title", ""),
                    url=item.get("href", "") or item.get("url", ""),
                    snippet=item.get("body", "")[:1500],
                    score=0.0,
                    backend="duckduckgo",
                ))
        return hits
    return await asyncio.to_thread(_run)


_BACKENDS = {
    "tavily":     _search_tavily,
    "exa":        _search_exa,
    "perplexity": _search_perplexity,
    "brave":      _search_brave,
    "serpapi":    _search_serpapi,
    "duckduckgo": _search_duckduckgo,
}


async def _search_with_fallback(query: str, limit: int,
                                 *, prefer: str = "") -> tuple[str, list[SearchHit]]:
    """Try `prefer` first, then walk the chain. Returns (backend_used, hits)."""
    chain = []
    if prefer and prefer in _BACKENDS:
        chain.append(prefer)
    chain.append(_available_backend())
    for fallback in ("tavily", "exa", "brave", "serpapi", "duckduckgo"):
        if fallback not in chain:
            chain.append(fallback)
    # Dedupe while preserving order
    seen: set[str] = set()
    chain = [b for b in chain if not (b in seen or seen.add(b))]

    for backend in chain:
        try:
            hits = await _BACKENDS[backend](query, limit)
            if hits:
                return backend, hits
        except Exception:
            continue
    return "none", []


# ── Tool 1: web_search ──────────────────────────────────────────────────────


async def _tool_web_search(args: dict[str, Any]) -> str:
    """Real-time web search. Returns NUMBERED hits with URL + snippet so
    the agent can cite by number."""
    query = (args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit", 6)), 10))
    if not query:
        return "ERROR: query required"
    backend, hits = await _search_with_fallback(query, limit,
                                                 prefer=str(args.get("backend", "")))
    if not hits:
        return (f"(no results found for '{query}'. "
                f"Tried backend: {_backend_label(backend)}. "
                f"DO NOT FABRICATE — tell the user no results came back.)")

    lines = [f"⟨◇⟩ search · {_backend_label(backend)} · {len(hits)} hits for: {query}\n"]
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h.title}")
        lines.append(f"    URL:     {h.url}")
        if h.score:
            lines.append(f"    Score:   {h.score:.2f}")
        if h.snippet:
            snip = h.snippet.replace("\n", " ").strip()[:500]
            lines.append(f"    Snippet: {snip}")
        lines.append("")
    lines.append("CITATION RULE: every claim in your reply MUST cite a hit "
                 "number above, e.g. [1] or [2,4]. If a fact isn't in any "
                 "snippet, DO NOT include it — say 'not found in sources'.")
    return "\n".join(lines)


# ── Tool 2: web_research (deep, multi-hop) ──────────────────────────────────


async def _tool_web_research(args: dict[str, Any]) -> str:
    """Deep research: searches, then fetches the top N pages in full,
    then returns combined source material. The agent synthesises ONLY
    from this material — never invents.

    Use this when the user asks for analysis, comparisons, or
    "what does the internet say about X" questions.
    """
    query = (args.get("query") or "").strip()
    if not query:
        return "ERROR: query required"
    n_search = max(3, min(int(args.get("limit", 5)), 8))
    fetch_top = max(2, min(int(args.get("fetch_top", 3)), 5))

    backend, hits = await _search_with_fallback(query, n_search)
    if not hits:
        return f"(no search results for '{query}'. Tell the user nothing came back.)"

    # Tavily already gives us raw_content; for other backends we fetch.
    needs_fetch = [h for h in hits[:fetch_top]
                   if not h.snippet or len(h.snippet) < 400]
    if needs_fetch:
        from argus.tools.scraping import _fetch_and_clean
        results = await asyncio.gather(
            *(_fetch_and_clean(h.url, timeout=15) for h in needs_fetch),
            return_exceptions=True,
        )
        for h, res in zip(needs_fetch, results):
            if isinstance(res, Exception):
                continue
            _, content = res
            if isinstance(content, str) and not content.startswith("ERROR"):
                h.snippet = content[:4000]

    # Build the dossier
    out = [
        f"⟨◇⟩ research dossier · backend: {_backend_label(backend)} · "
        f"{len(hits)} sources · {fetch_top} fully fetched\n",
        f"QUERY: {query}\n",
        "─" * 60 + "\n",
    ]
    for i, h in enumerate(hits, 1):
        out.append(f"[{i}] {h.title}")
        out.append(f"     URL: {h.url}")
        if h.snippet:
            body = h.snippet.replace("\n", " ").strip()[:2800]
            out.append(f"     {body}")
        out.append("")

    out.append("─" * 60)
    out.append(
        "SYNTHESIS RULES:\n"
        "  1. Every factual claim MUST cite a source number like [3] or [1,4].\n"
        "  2. If something isn't in any source above, say 'sources don't cover this' — "
        "DO NOT invent.\n"
        "  3. Quote verbatim for numbers, dates, names. Paraphrase only for context.\n"
        "  4. End your reply with a 'Sources:' list of the URLs you actually cited."
    )
    return "\n".join(out)


# ── Tool 3: web_fetch_clean (one-URL deep read) ─────────────────────────────


async def _tool_web_fetch_clean(args: dict[str, Any]) -> str:
    """Fetch a single URL and return clean Markdown — for when the agent
    knows exactly which page it wants to read."""
    url = (args.get("url") or "").strip()
    if not url:
        return "ERROR: url required"
    if not re.match(r"^https?://", url):
        url = "https://" + url
    from argus.tools.scraping import _fetch_and_clean
    title, text = await _fetch_and_clean(url, timeout=20)
    if text.startswith("ERROR"):
        return text
    return f"# {title or url}\nURL: {url}\n\n{text[:8000]}"


# ── Registration ────────────────────────────────────────────────────────────


def register_research_tools() -> None:
    """Replaces the legacy `web_search` with a backend-routed, citation-forced
    implementation. Adds `web_research` and `web_fetch_clean`."""
    # Replace the built-in web_search with our research-grade one
    register(ToolImpl("web", ToolSpec(
        name="web_search",
        description=(
            "Real-time web search with CITED snippets. Returns numbered hits, "
            "each with title + URL + raw snippet from the source. Use this for "
            "any 'find me X', 'is there a Y', 'who is Z' question. "
            "Auto-picks the best backend you have a key for: "
            "Tavily → Exa → Perplexity → Brave → SerpAPI → DuckDuckGo. "
            "CRITICAL: every fact in your reply MUST cite a hit number "
            "(e.g. [1]) or be flagged as 'not in sources'. NO fabrication."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer",
                          "description": "Max hits. Default 6, max 10."},
                "backend": {"type": "string",
                            "description": "Optional: force a specific backend "
                                           "(tavily | exa | perplexity | brave | serpapi | duckduckgo)"},
            },
            "required": ["query"],
        },
    ), _tool_web_search))

    register(ToolImpl("web", ToolSpec(
        name="web_research",
        description=(
            "DEEP web research: searches, fetches the top 3 pages in full, "
            "returns a dossier of source material with strict citation rules. "
            "Use this for analysis, comparisons, 'what does X think about Y' "
            "questions, or anything where the user wants real evidence. "
            "Anti-hallucination by construction — the agent synthesises ONLY "
            "from the returned sources and cites every claim."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query":     {"type": "string"},
                "limit":     {"type": "integer",
                              "description": "Total hits to surface. Default 5, max 8."},
                "fetch_top": {"type": "integer",
                              "description": "How many top hits to fully fetch + clean. Default 3, max 5."},
            },
            "required": ["query"],
        },
    ), _tool_web_research))

    register(ToolImpl("web", ToolSpec(
        name="web_fetch_clean",
        description=(
            "Fetch ONE URL and return clean Markdown content. Use when the "
            "agent already knows the exact URL it wants to read."
        ),
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    ), _tool_web_fetch_clean))
