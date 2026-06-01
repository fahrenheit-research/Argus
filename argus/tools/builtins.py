"""All built-in ARGUS tools in one file.

Design rules for Groq-compatible tool schemas:
- Keep schemas simple — no deeply nested `oneOf` / `anyOf`
- Prefer flat `properties` with `string` / `integer` / `boolean`
- `enum` is fine; `$ref` is not
- Keep `description` under 120 chars per property (Groq counts tokens)
- Never pass `additionalProperties: false` — Groq rejects it

Every tool is a plain async function + a clean ToolSpec.
call register_all_builtins() once from register_defaults().
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus import paths
from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, _APPROVED_CMDS, register


# ── helpers ──────────────────────────────────────────────────────────────────


def _ws() -> Path:
    ws = Path(os.path.expanduser("~/argus-workspace"))
    ws.mkdir(exist_ok=True)
    return ws


def _in_ws(rel: str) -> Path | None:
    target = (_ws() / rel).resolve()
    if _ws().resolve() not in target.parents and target != _ws().resolve():
        return None
    return target


# ── memory ────────────────────────────────────────────────────────────────────


async def _memory(args: dict[str, Any]) -> str:
    """Hermes-style single action-dispatched memory tool."""
    action = (args.get("action") or "").strip().lower()
    file_id = (args.get("file") or "memory").strip().lower()
    target = paths.USER_MD if file_id == "user" else paths.MEMORY_MD
    paths.ensure_dirs()

    if action == "read":
        if not target.exists():
            return "(memory is empty)"
        return target.read_text()[:8000]

    if action == "search":
        q = (args.get("query") or "").strip().lower()
        if not q:
            return "ERROR: empty query"
        hits: list[str] = []
        for p in (paths.USER_MD, paths.MEMORY_MD):
            if not p.exists():
                continue
            for line in p.read_text().splitlines():
                if q in line.lower():
                    hits.append(f"{p.name}: {line}")
                    if len(hits) >= 20:
                        break
        return "\n".join(hits) if hits else "(no matches)"

    if action == "add":
        content = (args.get("content") or "").strip()
        if not content:
            return "ERROR: no content"
        if target.exists() and target.stat().st_size > 32 * 1024:
            return "ERROR: memory at 32 KB cap — use replace/remove first"
        today = time.strftime("%Y-%m-%d")
        with target.open("a") as fh:
            fh.write(f"\n§ {today}\n{content}\n")
        return f"OK: added {len(content)} chars"

    if action == "replace":
        find = (args.get("find") or "").strip()
        content = (args.get("content") or "").strip()
        if not find or not content:
            return "ERROR: need 'find' and 'content'"
        if not target.exists():
            return "ERROR: file does not exist"
        text = target.read_text()
        if text.count(find) != 1:
            return f"ERROR: 'find' matches {text.count(find)} times — use a longer unique substring"
        target.write_text(text.replace(find, content))
        return "OK: replaced"

    if action == "remove":
        find = (args.get("find") or "").strip()
        if not find:
            return "ERROR: no 'find'"
        if not target.exists():
            return "ERROR: file does not exist"
        text = target.read_text()
        sections = text.split("§")
        kept = [s for s in sections if find not in s]
        if len(kept) == len(sections):
            return "ERROR: no matching entry"
        target.write_text("§".join(kept))
        return f"OK: removed entry containing '{find[:40]}'"

    return f"ERROR: unknown action '{action}'"


# ── web search (DuckDuckGo — free, no API key) ────────────────────────────────


async def _web_search(args: dict[str, Any]) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "ERROR: no query"
    n = min(int(args.get("n", 5)), 10)
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # fallback to legacy name
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=n):
                results.append(f"**{r['title']}**\n{r['href']}\n{r['body'][:300]}")
        return "\n\n---\n\n".join(results) if results else "(no results)"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ── web fetch ─────────────────────────────────────────────────────────────────


async def _web_fetch(args: dict[str, Any]) -> str:
    url = (args.get("url") or "").strip()
    if not url:
        return "ERROR: no url"
    timeout = float(args.get("timeout", 15))
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "ARGUS/0.1 (+f-r.co)"})
        return f"HTTP {r.status_code}\n{r.text[:6000]}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ── arxiv search ──────────────────────────────────────────────────────────────


async def _arxiv_search(args: dict[str, Any]) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "ERROR: no query"
    n = min(int(args.get("n", 5)), 20)
    import urllib.parse, urllib.request
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query=all:{urllib.parse.quote(query)}&start=0&max_results={n}"
    )
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
        import re
        titles = re.findall(r"<title>(.+?)</title>", r.text)
        ids = re.findall(r"<id>http://arxiv.org/abs/([^<]+)</id>", r.text)
        summaries = re.findall(r"<summary>(.+?)</summary>", r.text, re.S)
        results = []
        for t, i, s in zip(titles[1:], ids, summaries):
            results.append(f"**{t.strip()}**\nhttps://arxiv.org/abs/{i}\n{s.strip()[:200]}")
        return "\n\n---\n\n".join(results) if results else "(no results)"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ── calculator ────────────────────────────────────────────────────────────────


async def _calculator(args: dict[str, Any]) -> str:
    expr = (args.get("expression") or "").strip()
    if not expr:
        return "ERROR: no expression"
    try:
        import ast, math, operator
        allowed = {
            **{k: v for k, v in vars(math).items() if not k.startswith("_")},
            "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
            "pow": pow, "int": int, "float": float,
        }
        tree = ast.parse(expr, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Call)):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.id if isinstance(func, ast.Name) else str(func)
                    if name not in allowed:
                        return f"ERROR: function '{name}' not allowed"
        result = eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, allowed)
        return str(result)
    except Exception as e:
        return f"ERROR: {e}"


# ── datetime ──────────────────────────────────────────────────────────────────


async def _datetime_tool(args: dict[str, Any]) -> str:
    tz = (args.get("timezone") or "UTC").strip()
    try:
        import zoneinfo
        zone = zoneinfo.ZoneInfo(tz)
        now = datetime.now(zone)
    except Exception:
        now = datetime.now(timezone.utc)
        tz = "UTC"
    return f"{now.strftime('%Y-%m-%d %H:%M:%S %Z')} ({tz})"


# ── check_telegram ────────────────────────────────────────────────────────────


async def _check_telegram(args: dict[str, Any]) -> str:
    """Live check: call Telegram getMe and report bot identity + allow-list."""
    from argus import config as _config
    cfg = _config.load()
    token = _config.resolve_secret("TELEGRAM_BOT_TOKEN", cfg)
    if not token:
        return (
            "TELEGRAM_BOT_TOKEN is not set.\n"
            "Fix: type `/telegram` in chat to configure it."
        )
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
        data = r.json()
        if not data.get("ok"):
            return f"Telegram returned not-ok: {data.get('description', 'unknown error')}"
        bot = data["result"]
        allowed_raw = _config.resolve_secret("TELEGRAM_ALLOWED_USERS", cfg) or ""
        allowed_count = len([s for s in allowed_raw.split(",") if s.strip().isdigit()])
        return (
            f"✓ Telegram bot is live: @{bot['username']} ({bot['first_name']})\n"
            f"  Allow-list: {allowed_count} user(s) permitted\n"
            f"  Status: {'ready — `argus gateway start` to connect' if allowed_count else 'no users in allow-list — type /telegram to add yourself'}"
        )
    except Exception as e:
        return f"ERROR calling Telegram API: {type(e).__name__}: {e}"


# ── get_status ────────────────────────────────────────────────────────────────


async def _get_status(args: dict[str, Any]) -> str:
    """Return full ARGUS configuration: provider, Telegram bot, user IDs, memory."""
    from argus import config as _config
    cfg = _config.load()
    from argus.data.providers import get_provider

    info = get_provider(cfg.agent.default_provider)
    has_key = _config.has_secret(info.env_var, cfg)
    has_tg = _config.has_secret("TELEGRAM_BOT_TOKEN", cfg)
    allowed_raw = _config.resolve_secret("TELEGRAM_ALLOWED_USERS", cfg) or ""
    allowed_ids = [s.strip() for s in allowed_raw.split(",") if s.strip().isdigit()]

    lines = [
        f"Provider: {info.label} — key {'✓ set' if has_key else '✗ missing'}",
        f"Model:    {cfg.agent.default_model}",
        f"Toolsets: {', '.join(cfg.agent.toolsets)}",
        f"Telegram: {'✓ token set' if has_tg else '✗ no token'}",
        f"Allowed Telegram user IDs: {', '.join(allowed_ids) if allowed_ids else '(none configured — run: argus gateway allow <id>)'}",
        f"Memory:   {paths.MEMORY_MD.stat().st_size if paths.MEMORY_MD.exists() else 0} bytes",
        f"Skills:   {len(list(paths.SKILLS_DIR.glob('*/SKILL.md')))} installed",
    ]
    return "\n".join(lines)


# ── file ops ──────────────────────────────────────────────────────────────────


async def _read_file(args: dict[str, Any]) -> str:
    rel = (args.get("path") or "").strip()
    if not rel:
        return "ERROR: no path"
    target = _in_ws(rel)
    if target is None:
        return "ERROR: path escapes workspace"
    if not target.exists():
        return "ERROR: file not found"
    return target.read_text()[:8000]


async def _write_file(args: dict[str, Any]) -> str:
    rel = (args.get("path") or "").strip()
    content = args.get("content", "")
    if not rel:
        return "ERROR: no path"
    target = _in_ws(rel)
    if target is None:
        return "ERROR: path escapes workspace"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"OK: wrote {len(content)} bytes to {target.relative_to(_ws())}"


async def _list_dir(args: dict[str, Any]) -> str:
    rel = (args.get("path") or ".").strip()
    target = _in_ws(rel)
    if target is None:
        return "ERROR: path escapes workspace"
    if not target.exists():
        return "ERROR: directory not found"
    return "\n".join(sorted(p.name for p in target.iterdir()))


# ── run_command ───────────────────────────────────────────────────────────────


async def _run_command(args: dict[str, Any]) -> str:
    cmd = (args.get("command") or "").strip()
    if not cmd:
        return "ERROR: no command"
    env_approved = os.environ.get("ARGUS_SHELL_APPROVED", "").strip()
    if cmd not in _APPROVED_CMDS and env_approved != cmd:
        return (
            f"DENIED: not approved. The user must type exactly this in the chat:\n"
            f"    /approve {cmd}\n"
            f"Then ask again."
        )
    _APPROVED_CMDS.discard(cmd)
    try:
        out = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30,
            cwd=str(_ws()),
        )
        return f"exit={out.returncode}\n{out.stdout[:4000]}\n{out.stderr[:1000]}"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 30s"


# ── registration ──────────────────────────────────────────────────────────────


def register_all_builtins() -> None:
    """Register every built-in tool. Idempotent — safe to call twice."""

    register(ToolImpl("memory", ToolSpec(
        name="memory",
        description="Read/write MEMORY.md (free-form notes) and USER.md (profile). Actions: read|add|replace|remove|search.",
        parameters={
            "type": "object",
            "properties": {
                "action":  {"type": "string", "enum": ["read", "add", "replace", "remove", "search"], "description": "What to do"},
                "file":    {"type": "string", "enum": ["memory", "user"], "description": "Which file (default: memory)"},
                "content": {"type": "string", "description": "Text to add or the replacement content"},
                "find":    {"type": "string", "description": "Short unique substring to replace or remove"},
                "query":   {"type": "string", "description": "Search term"},
            },
            "required": ["action"],
        },
    ), _memory))

    register(ToolImpl("web", ToolSpec(
        name="web_search",
        description="Search the web with DuckDuckGo. Returns titles, URLs, and snippets. Free, no API key.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "n":     {"type": "integer", "description": "Number of results (default 5, max 10)"},
            },
            "required": ["query"],
        },
    ), _web_search))

    register(ToolImpl("web", ToolSpec(
        name="web_fetch",
        description="HTTP GET a URL and return the body as plain text. Good for docs, APIs, and pages.",
        parameters={
            "type": "object",
            "properties": {
                "url":     {"type": "string", "description": "Absolute URL to fetch"},
                "timeout": {"type": "number", "description": "Seconds before giving up (default 15)"},
            },
            "required": ["url"],
        },
    ), _web_fetch))

    register(ToolImpl("web", ToolSpec(
        name="arxiv_search",
        description="Search arXiv.org for academic papers. Returns title, URL, and abstract.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Research topic or keywords"},
                "n":     {"type": "integer", "description": "Number of results (default 5)"},
            },
            "required": ["query"],
        },
    ), _arxiv_search))

    register(ToolImpl("web", ToolSpec(
        name="calculator",
        description="Evaluate a math expression. Supports standard math functions (sin, cos, sqrt, log, etc.).",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression to evaluate, e.g. 'sqrt(144) + log(10)'"},
            },
            "required": ["expression"],
        },
    ), _calculator))

    register(ToolImpl("web", ToolSpec(
        name="get_datetime",
        description="Get the current date and time in any timezone.",
        parameters={
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "IANA timezone name, e.g. 'Asia/Kolkata', 'America/New_York' (default: UTC)"},
            },
            "required": [],
        },
    ), _datetime_tool))

    register(ToolImpl("memory", ToolSpec(
        name="check_telegram",
        description="Check if the Telegram bot is configured and reachable. Calls Telegram getMe API and reports bot identity and allow-list status.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ), _check_telegram))

    register(ToolImpl("memory", ToolSpec(
        name="get_status",
        description="Get current ARGUS configuration: provider, model, API key status, Telegram status, memory size, installed skills.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ), _get_status))

    register(ToolImpl("files", ToolSpec(
        name="read_file",
        description="Read a file inside ~/argus-workspace. Returns first 8000 chars.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path inside ~/argus-workspace"}},
            "required": ["path"],
        },
    ), _read_file))

    register(ToolImpl("files", ToolSpec(
        name="write_file",
        description="Write content to a file inside ~/argus-workspace. Creates parent dirs.",
        parameters={
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Relative path inside ~/argus-workspace"},
                "content": {"type": "string", "description": "File contents to write"},
            },
            "required": ["path", "content"],
        },
    ), _write_file))

    register(ToolImpl("files", ToolSpec(
        name="list_dir",
        description="List files in a directory inside ~/argus-workspace.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path (default '.')"}},
            "required": [],
        },
    ), _list_dir))

    register(ToolImpl("shell", ToolSpec(
        name="run_command",
        description="Run a shell command inside ~/argus-workspace. Requires prior /approve <command> from the user.",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Shell command to execute"}},
            "required": ["command"],
        },
    ), _run_command))
