"""Tool registry — maps tool name → (impl, spec, toolset).

Self-learning lives here: `memory_write` is a real tool the agent can
call to append durable facts to MEMORY.md. The system prompt tells the
agent when to use it. That's "self-learning" in the Hermes sense — a
durable Markdown journal grows over time without manual editing.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from argus import paths
from argus.providers.base import ToolSpec


Handler = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class ToolImpl:
    toolset: str
    spec: ToolSpec
    handler: Handler


_REGISTRY: dict[str, ToolImpl] = {}


def register(impl: ToolImpl) -> None:
    _REGISTRY[impl.spec.name] = impl


def get_tool(name: str) -> ToolImpl | None:
    return _REGISTRY.get(name)


def available_tools() -> list[ToolImpl]:
    return list(_REGISTRY.values())


def tools_for(toolsets: list[str], disabled: list[str] | None = None) -> list[ToolImpl]:
    """Return the tool impls allowed for the active toolset list."""
    disabled = set(disabled or [])
    allow = set(toolsets) - disabled
    return [t for t in _REGISTRY.values() if t.toolset in allow]


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------


async def _memory(args: dict[str, Any]) -> str:
    """Hermes-style single memory tool, action-dispatched.

    Actions:
      - read              → return MEMORY.md (or USER.md if file="user")
      - add(content)      → append a new entry, prefixed with § delimiter
      - replace(find, content) → swap an entry matched by short unique substring
      - remove(find)      → remove an entry matched by short unique substring
      - search(query)     → substring search across both files

    Files are bounded by character limits, not tokens — model-independent
    and stable across providers. The agent decides what's worth persisting;
    the system prompt nudges it to write durable facts (preferences,
    decisions, ongoing projects) and skip transient context.
    """
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
        # Bound MEMORY.md at 32 KB so it never dominates the prompt.
        if target.exists() and target.stat().st_size > 32 * 1024:
            return "ERROR: memory file at 32 KB cap — use 'replace' or 'remove' first"
        today = time.strftime("%Y-%m-%d")
        entry = f"\n§ {today}\n{content}\n"
        with target.open("a") as fh:
            fh.write(entry)
        return f"OK: added {len(content)} chars"

    if action == "replace":
        find = (args.get("find") or "").strip()
        content = (args.get("content") or "").strip()
        if not find or not content:
            return "ERROR: need both 'find' and 'content'"
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
        if text.count(find) != 1:
            return f"ERROR: 'find' matches {text.count(find)} times — use a longer unique substring"
        # Remove the whole § entry containing `find`, not just the substring.
        sections = text.split("§")
        kept = [s for s in sections if find not in s]
        if len(kept) == len(sections):
            return "ERROR: no matching entry"
        target.write_text("§".join(kept))
        return f"OK: removed entry containing '{find[:40]}…'"

    return f"ERROR: unknown action '{action}' — use read|add|replace|remove|search"


async def _web_fetch(args: dict[str, Any]) -> str:
    """Fetch a URL and return the textual body (no JS execution)."""
    url = args.get("url", "")
    if not url:
        return "ERROR: no url"
    timeout = float(args.get("timeout", 15))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "ARGUS/0.1 (+f-r.co)"})
        snippet = r.text[:4000]
        return f"HTTP {r.status_code}\n{snippet}"
    except httpx.HTTPError as e:
        return f"ERROR: {e}"


async def _read_file(args: dict[str, Any]) -> str:
    workspace = Path(os.path.expanduser("~/argus-workspace"))
    workspace.mkdir(exist_ok=True)
    rel = args.get("path", "")
    if not rel:
        return "ERROR: no path"
    target = (workspace / rel).resolve()
    if workspace.resolve() not in target.parents and target != workspace.resolve():
        return f"ERROR: path escapes workspace ({workspace})"
    if not target.exists():
        return "ERROR: file not found"

    # Guard against binary files (DOCX, XLSX, PDF, etc.) — trying to
    # read them as UTF-8 raises UnicodeDecodeError and provides zero value.
    # Instead, surface a useful message so the agent knows what to do.
    _BINARY_EXTENSIONS = {
        ".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".pptx", ".ppt",
        ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
        ".mp3", ".mp4", ".wav", ".ogg", ".aiff", ".m4a",
        ".exe", ".bin", ".so", ".dylib",
    }
    if target.suffix.lower() in _BINARY_EXTENSIONS:
        size_kb = target.stat().st_size / 1024
        return (f"BINARY FILE — cannot read {target.name} ({size_kb:.0f} KB) as text.\n\n"
                f"This is a binary document. It was already written to disk at:\n"
                f"  {target}\n\n"
                f"In Telegram: the file was sent as a document attachment automatically.\n"
                f"In CLI: open it with the appropriate application (Word / Excel / Acrobat).\n\n"
                f"DO NOT attempt to read binary files — just confirm the file was delivered.")

    try:
        return target.read_text()[:8000]
    except UnicodeDecodeError:
        # Unknown binary format — surface a helpful error rather than a crash
        size_kb = target.stat().st_size / 1024
        return (f"BINARY FILE — {target.name} ({size_kb:.0f} KB) is not readable as text. "
                f"It was written to {target} — deliver it as a file attachment.")


async def _write_file(args: dict[str, Any]) -> str:
    workspace = Path(os.path.expanduser("~/argus-workspace"))
    workspace.mkdir(exist_ok=True)
    rel = args.get("path", "")
    content = args.get("content", "")
    if not rel:
        return "ERROR: no path"
    target = (workspace / rel).resolve()
    if workspace.resolve() not in target.parents and target.parent != workspace.resolve():
        return f"ERROR: path escapes workspace ({workspace})"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"OK: wrote {len(content)} bytes to {target.relative_to(workspace)}"


async def _list_dir(args: dict[str, Any]) -> str:
    workspace = Path(os.path.expanduser("~/argus-workspace"))
    workspace.mkdir(exist_ok=True)
    rel = args.get("path", ".")
    target = (workspace / rel).resolve()
    if workspace.resolve() not in target.parents and target != workspace.resolve():
        return f"ERROR: path escapes workspace ({workspace})"
    if not target.exists():
        return "ERROR: directory not found"
    return "\n".join(sorted(p.name for p in target.iterdir()))


_APPROVED_CMDS: set[str] = set()


def approve_command(cmd: str) -> None:
    """Called by the `/approve <cmd>` slash handler. Single-use — the
    approval is consumed by the next matching `run_command` call."""
    _APPROVED_CMDS.add(cmd.strip())


def clear_approvals() -> None:
    _APPROVED_CMDS.clear()


async def _run_command(args: dict[str, Any]) -> str:
    """PRD §15.3: shell toolset requires explicit approval. Approvals
    come from the chat REPL's `/approve <command>` slash command; this
    handler refuses unless the exact command text was approved this
    session. Approval is single-use — consumed on first match."""
    cmd = (args.get("command") or "").strip()
    if not cmd:
        return "ERROR: no command"

    # Accept either an in-process approval (CLI REPL) OR the env-var
    # fallback (handy for non-REPL contexts like Telegram inline approval).
    env_approved = os.environ.get("ARGUS_SHELL_APPROVED", "").strip()
    if cmd not in _APPROVED_CMDS and env_approved != cmd:
        return (
            f"DENIED: shell command not approved by the user. "
            f"The user must type EXACTLY this in the chat:\n"
            f"    /approve {cmd}\n"
            f"Then re-ask you to run the command."
        )

    # Consume the approval.
    _APPROVED_CMDS.discard(cmd)

    try:
        out = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30,
            cwd=os.path.expanduser("~/argus-workspace"),
        )
        return f"exit={out.returncode}\nstdout:\n{out.stdout[:4000]}\nstderr:\n{out.stderr[:1000]}"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 30s"


# ---------------------------------------------------------------------------
# Specs (vendor-neutral JSON schemas)
# ---------------------------------------------------------------------------


def register_defaults() -> None:
    """Idempotent — safe to call at every chat start."""
    if _REGISTRY:
        return

    # All built-in tools with clean Groq-compatible schemas.
    from argus.tools.builtins import register_all_builtins
    register_all_builtins()

    # AgentBrain typed knowledge graph.
    from argus.tools.graph import register_graph_tool
    register_graph_tool()

    # Sub-agent delegation (Hermes pattern). One level of nesting only.
    from argus.tools.delegate import register_delegate_tool
    register_delegate_tool()

    # Multi-model orchestration pipeline.
    from argus.tools.orchestrate import register_orchestrate_tool
    register_orchestrate_tool()

    # Autonomous API onboarding — fetches docs, detects auth, persists key.
    from argus.tools.api_setup import register_api_setup_tool
    register_api_setup_tool()

    # Hermes-Agent native ports — patch, search_files, execute_code, todo,
    # clarify, web_extract, text_to_speech, vision_analyze.
    from argus.tools.hermes_ports import register_hermes_ports
    register_hermes_ports()

    # Constellation swarm — autonomous role-specialised agent team.
    from argus.tools.constellation_tool import register_constellation_tool
    register_constellation_tool()

    # Connector action tools — Gmail send/search/read/draft/list.
    from argus.tools.gmail_tools import register_gmail_tools
    register_gmail_tools()

    # Scraping — lightweight httpx+BS4+LLM by default, optional ScrapeGraphAI.
    from argus.tools.scraping import register_scraping_tools
    register_scraping_tools()

    # Research engine — Tavily/Exa/Perplexity/Brave/SerpAPI/DDG with strict
    # citation rules. Overrides the legacy web_search above with the
    # anti-hallucination version (last-registered wins by name).
    from argus.tools.research import register_research_tools
    register_research_tools()

    # Scribe — Word + PDF authoring (Scribe specialist role uses these)
    from argus.tools.scribe import register_scribe_tools
    register_scribe_tools()

    # Ledger — XLSX + CSV + pivots (Ledger specialist role uses these)
    from argus.tools.ledger import register_ledger_tools
    register_ledger_tools()

    register(ToolImpl("memory", ToolSpec(
        name="memory",
        description=(
            "Read/write the durable MEMORY.md journal and USER.md profile.\n"
            "Use 'add' ONLY for facts that matter beyond this session — preferences, "
            "decisions, people, ongoing projects. Never for transient context.\n"
            "Use 'replace'/'remove' when you have a short unique substring of an entry."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "add", "replace", "remove", "search"]},
                "file":   {"type": "string", "enum": ["memory", "user"], "description": "Default 'memory'"},
                "content": {"type": "string", "description": "Body for add/replace"},
                "find":    {"type": "string", "description": "Short unique substring for replace/remove"},
                "query":   {"type": "string", "description": "For search"},
            },
            "required": ["action"],
        },
    ), _memory))

    register(ToolImpl("web", ToolSpec(
        name="web_fetch",
        description="HTTP GET a URL and return the response body as text (HTML stays HTML; no JS execution).",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute URL"},
                "timeout": {"type": "number", "description": "Seconds; default 15"},
            },
            "required": ["url"],
        },
    ), _web_fetch))

    register(ToolImpl("files", ToolSpec(
        name="read_file",
        description="Read a file inside ~/argus-workspace. Returns the first 8000 chars.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ), _read_file))

    register(ToolImpl("files", ToolSpec(
        name="write_file",
        description="Write a file inside ~/argus-workspace, creating parent dirs.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    ), _write_file))

    register(ToolImpl("files", ToolSpec(
        name="list_dir",
        description="List entries in a directory under ~/argus-workspace.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Default '.'"}},
            "required": [],
        },
    ), _list_dir))

    register(ToolImpl("shell", ToolSpec(
        name="run_command",
        description=(
            "Run a shell command inside ~/argus-workspace. Requires per-call approval — "
            "if the user has not approved this exact command, this returns DENIED and you "
            "should ask the user to approve it."
        ),
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    ), _run_command))
