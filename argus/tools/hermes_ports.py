"""Native ports of the high-value Hermes-Agent built-in tools.

Catalog ported (8 tools, 4 new toolsets):

  • patch              [files]   — fuzzy find/replace inside ~/argus-workspace
  • search_files       [files]   — ripgrep-backed name+content search
  • execute_code       [code]    — run a Python snippet in an isolated subprocess
  • todo               [todo]    — session task list (JSON on disk, persists)
  • clarify            [clarify] — ask the user a question, return their answer
  • web_extract        [web]     — fetch URL → markdown; summarises long pages
  • text_to_speech     [voice]   — synthesise speech via argus.voice.tts
  • vision_analyze     [vision]  — describe an image via the configured vision LLM

Each handler is fully self-contained and async. Tools are registered via
`register_hermes_ports()` which is idempotent (safe to re-call).

Design parity with Hermes:
  - Same tool names where reasonable (so prompts/skills transfer cleanly)
  - Same shape: a single `action` field where the upstream tool dispatched
  - Honours workspace sandbox (`~/argus-workspace`) for any FS write
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from argus import paths
from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


# ── Workspace helper (shared with builtins) ──────────────────────────────────


_WORKSPACE = Path(os.path.expanduser("~/argus-workspace"))


def _safe_path(rel: str) -> Path | None:
    """Resolve `rel` inside the workspace, returning None on escape attempts."""
    _WORKSPACE.mkdir(exist_ok=True)
    target = (_WORKSPACE / rel).resolve()
    base = _WORKSPACE.resolve()
    if base not in target.parents and target != base:
        return None
    return target


# ── 1. patch — fuzzy find-and-replace ───────────────────────────────────────


async def _patch(args: dict[str, Any]) -> str:
    """Hermes-style `patch` tool: locate old_text in a file, replace with new_text.

    Matches Hermes' contract: old_text must be a unique substring of the file
    body (no fuzzy regex magic; that's the LLM's job). If it isn't unique,
    we report a count so the agent can supply more context.
    """
    rel     = (args.get("file_path") or "").strip()
    old_txt = args.get("old_text", "")
    new_txt = args.get("new_text", "")

    if not rel:
        return "ERROR: file_path required"
    if not old_txt:
        return "ERROR: old_text required (use write_file to create or fully overwrite)"

    target = _safe_path(rel)
    if target is None:
        return f"ERROR: path escapes workspace ({_WORKSPACE})"
    if not target.exists():
        return f"ERROR: file not found — {rel}"

    body  = target.read_text()
    count = body.count(old_txt)
    if count == 0:
        return ("ERROR: old_text not found in file. Re-read the file with "
                "read_file, then patch with the EXACT current text.")
    if count > 1:
        return (f"ERROR: old_text matched {count} times — provide more "
                "surrounding context so it's unique.")

    target.write_text(body.replace(old_txt, new_txt, 1))
    delta = len(new_txt) - len(old_txt)
    return f"OK: patched {rel} ({delta:+d} bytes)"


# ── 2. search_files — ripgrep-backed search ─────────────────────────────────


async def _search_files(args: dict[str, Any]) -> str:
    """Ripgrep-backed name OR content search inside the workspace.

    Falls back to a pure-Python walk if `rg` isn't on PATH.
    """
    query  = (args.get("query") or "").strip()
    target = (args.get("target") or "content").strip().lower()
    rel    = (args.get("path") or ".").strip()

    if not query:
        return "ERROR: query required"
    if target not in ("content", "name"):
        return "ERROR: target must be 'content' or 'name'"

    root = _safe_path(rel)
    if root is None or not root.exists():
        return f"ERROR: path not found inside workspace — {rel}"

    rg = shutil.which("rg")
    hits: list[str] = []

    if rg and target == "content":
        proc = await asyncio.to_thread(
            subprocess.run,
            [rg, "--no-heading", "--line-number", "--color=never",
             "--max-count", "20", "--max-filesize", "1M", query, str(root)],
            capture_output=True, text=True, timeout=20,
        )
        for line in proc.stdout.splitlines()[:120]:
            hits.append(line.replace(str(root) + "/", ""))
    elif rg and target == "name":
        proc = await asyncio.to_thread(
            subprocess.run,
            [rg, "--files", str(root)],
            capture_output=True, text=True, timeout=20,
        )
        for line in proc.stdout.splitlines():
            if query.lower() in Path(line).name.lower():
                hits.append(line.replace(str(root) + "/", ""))
                if len(hits) >= 120: break
    else:
        # Pure-Python fallback
        for p in root.rglob("*"):
            if not p.is_file(): continue
            if target == "name":
                if query.lower() in p.name.lower():
                    hits.append(str(p.relative_to(_WORKSPACE)))
            else:
                try:
                    if p.stat().st_size > 1_000_000: continue
                    for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                        if query in line:
                            hits.append(f"{p.relative_to(_WORKSPACE)}:{i}:{line[:200]}")
                            if len(hits) >= 120: break
                except OSError:
                    continue
            if len(hits) >= 120: break

    if not hits:
        return f"(no matches for '{query}' in {target} under {rel})"
    return "\n".join(hits)


# ── 3. execute_code — sandboxed Python ───────────────────────────────────────


async def _execute_code(args: dict[str, Any]) -> str:
    """Run a Python snippet in a fresh subprocess.

    Safety: sandboxed to the workspace cwd, 15-second wall clock, network
    is NOT blocked (the agent often needs it). Stdout+stderr captured.
    """
    script = args.get("script", "")
    if not script:
        return "ERROR: script required"

    _WORKSPACE.mkdir(exist_ok=True)
    timeout = float(args.get("timeout", 15))

    # Write to a tempfile so multi-line scripts work without quoting hell.
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     dir=str(_WORKSPACE)) as fh:
        fh.write(script)
        script_path = fh.name

    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(_WORKSPACE),
        )
        out = (proc.stdout or "")[:4000]
        err = (proc.stderr or "")[:2000]
        result = f"exit={proc.returncode}\n"
        if out: result += f"stdout:\n{out}\n"
        if err: result += f"stderr:\n{err}\n"
        return result.strip()
    except subprocess.TimeoutExpired:
        return f"ERROR: script exceeded {timeout}s wall-clock"
    finally:
        try: os.unlink(script_path)
        except OSError: pass


# ── 4. todo — session task list ──────────────────────────────────────────────


_TODO_FILE = paths.HOME / "todos.json"


async def _todo(args: dict[str, Any]) -> str:
    """Hermes-shape `todo` tool. Actions: add, list, complete, remove, clear.

    Stored as JSON in ~/.argus/todos.json — persists across sessions but is
    cheap enough to dump in full on every change.
    """
    paths.ensure_dirs()
    action = (args.get("action") or "list").strip().lower()

    todos: list[dict] = []
    if _TODO_FILE.exists():
        try:
            todos = json.loads(_TODO_FILE.read_text())
        except json.JSONDecodeError:
            todos = []

    if action == "list":
        if not todos:
            return "(no todos)"
        lines = []
        for t in todos:
            mark = "✓" if t.get("done") else "○"
            lines.append(f"{mark} #{t['id']:>2}  {t['text']}")
        return "\n".join(lines)

    if action == "add":
        text = (args.get("text") or "").strip()
        if not text:
            return "ERROR: text required"
        next_id = (max((t["id"] for t in todos), default=0) + 1)
        todos.append({"id": next_id, "text": text, "done": False,
                      "created_at": int(time.time())})
        _TODO_FILE.write_text(json.dumps(todos, indent=2))
        return f"OK: added #{next_id} — {text}"

    if action == "complete":
        tid = int(args.get("id", 0))
        for t in todos:
            if t["id"] == tid:
                t["done"] = True
                _TODO_FILE.write_text(json.dumps(todos, indent=2))
                return f"OK: completed #{tid}"
        return f"ERROR: no todo with id={tid}"

    if action == "remove":
        tid = int(args.get("id", 0))
        new = [t for t in todos if t["id"] != tid]
        if len(new) == len(todos):
            return f"ERROR: no todo with id={tid}"
        _TODO_FILE.write_text(json.dumps(new, indent=2))
        return f"OK: removed #{tid}"

    if action == "clear":
        if _TODO_FILE.exists():
            _TODO_FILE.unlink()
        return "OK: cleared all todos"

    return f"ERROR: unknown action '{action}' — use list|add|complete|remove|clear"


# ── 5. clarify — ask the user a question ─────────────────────────────────────


async def _clarify(args: dict[str, Any]) -> str:
    """In CLI: blocks for stdin reply (with optional numbered choices).
    In gateway contexts (no TTY): returns a soft-fail so the agent commits.
    """
    question = (args.get("question") or "").strip()
    choices  = args.get("choices") or []

    if not question:
        return "ERROR: question required"
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return ("CLARIFY_UNAVAILABLE: no interactive TTY (running under a "
                "gateway/cron). Pick the most reasonable default and proceed; "
                "explain your choice in the final answer.")

    # CLI: print + read one line
    sys.stdout.write(f"\n  ❓  {question}\n")
    if isinstance(choices, list) and choices:
        for i, c in enumerate(choices[:4], 1):
            sys.stdout.write(f"     {i}. {c}\n")
        sys.stdout.write(f"  ▸ ")
        sys.stdout.flush()
        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt):
            return "CLARIFY_CANCELLED"
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return str(choices[int(answer) - 1])
        return answer
    sys.stdout.write(f"  ▸ ")
    sys.stdout.flush()
    try:
        return input().strip()
    except (EOFError, KeyboardInterrupt):
        return "CLARIFY_CANCELLED"


# ── 6. web_extract — URL → markdown ──────────────────────────────────────────


async def _web_extract(args: dict[str, Any]) -> str:
    """Fetch a URL and return clean Markdown.

    PDF detection via Content-Type; HTML stripped of nav/scripts/styles by a
    tiny inline cleaner (avoids hard dep on readability-lxml).
    """
    import httpx
    url = (args.get("url") or "").strip()
    if not url:
        return "ERROR: url required"

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "ARGUS/0.1 web-extract"}) as c:
            r = await c.get(url)
    except httpx.HTTPError as e:
        return f"ERROR: {e}"

    ctype = r.headers.get("content-type", "").lower()
    body  = r.text

    if "pdf" in ctype or url.lower().endswith(".pdf"):
        # Try pypdf if installed; otherwise tell the agent to use vision.
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(r.content))
            txt = "\n".join((p.extract_text() or "") for p in reader.pages[:30])
            return f"# PDF: {url}\n\n{txt[:8000]}"
        except ImportError:
            return f"(PDF detected; install pypdf for extraction. URL: {url})"

    # HTML → markdown (cheap inline cleaner)
    body = re.sub(r"<script[\s\S]*?</script>", "", body, flags=re.IGNORECASE)
    body = re.sub(r"<style[\s\S]*?</style>",  "", body, flags=re.IGNORECASE)
    body = re.sub(r"<nav[\s\S]*?</nav>",      "", body, flags=re.IGNORECASE)
    body = re.sub(r"<footer[\s\S]*?</footer>", "", body, flags=re.IGNORECASE)
    body = re.sub(r"<header[\s\S]*?</header>", "", body, flags=re.IGNORECASE)
    body = re.sub(r"<aside[\s\S]*?</aside>",   "", body, flags=re.IGNORECASE)
    # Tags → spaces, collapse whitespace
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;",  "<", text)
    text = re.sub(r"&gt;",  ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"\s+", " ", text).strip()

    return f"# {url}\n\n{text[:8000]}"


# ── 7. text_to_speech — voice wrapper ───────────────────────────────────────


async def _text_to_speech(args: dict[str, Any]) -> str:
    """Synthesize speech AND deliver it to the active surface.

    Context-aware:
      • In Telegram   → sends send_voice (OGG) or send_audio (MP3) directly,
                        returns "OK: sent voice/audio message".
      • In CLI        → writes to disk, returns the path so the agent can
                        mention it / the caller can play it.
      • In any other  → writes to disk + returns path.
    """
    from argus.voice import synthesize, available
    from argus import config as _config
    from argus.platform_context import get_platform_context
    import shutil as _sh

    text = (args.get("text") or "").strip()
    if not text:
        return "ERROR: text required"
    cfg = _config.load()
    provider = (args.get("provider") or cfg.voice.tts_provider).strip()
    voice    = (args.get("voice")    or cfg.voice.tts_voice).strip()

    # Format selection — caller can override; otherwise pick by context.
    explicit_fmt = (args.get("format") or "").strip().lower()
    pctx = get_platform_context()
    if explicit_fmt:
        fmt = explicit_fmt
    elif pctx and pctx.supports_audio_send:
        # Telegram: prefer OGG (voice bubble) when ffmpeg is present,
        # else MP3 via lameenc (audio bubble) — never raw WAV.
        fmt = "ogg" if _sh.which("ffmpeg") else "mp3"
    else:
        # CLI: prefer MP3 (most players handle it) when no ffmpeg.
        fmt = "ogg" if _sh.which("ffmpeg") else "mp3"

    if provider != cfg.voice.tts_provider:
        cfg.voice.tts_provider = provider
    ok, detail = available(provider)
    if not ok:
        return f"ERROR: provider '{provider}' not ready — {detail}"

    result = await synthesize(text, cfg=cfg, voice=voice, output_format=fmt)
    if not result:
        return "ERROR: synthesis failed (no audio produced)"

    # ── If we're in Telegram, ship the audio NOW ─────────────────────
    if pctx and pctx.supports_audio_send and result.path.exists():
        try:
            with result.path.open("rb") as fh:
                if result.format == "ogg":
                    await pctx.bot.send_voice(
                        chat_id=pctx.chat_id, voice=fh,
                        duration=int(result.duration_ms / 1000) or None,
                    )
                    return (f"OK: sent voice bubble ({result.duration_ms/1000:.1f}s, "
                            f"{result.path.stat().st_size:,} bytes, OGG/Opus)")
                else:
                    await pctx.bot.send_audio(
                        chat_id=pctx.chat_id, audio=fh,
                        duration=int(result.duration_ms / 1000) or None,
                        title="ARGUS",
                    )
                    return (f"OK: sent audio file ({result.duration_ms/1000:.1f}s, "
                            f"{result.path.stat().st_size:,} bytes, {result.format.upper()})")
        except Exception as e:  # noqa: BLE001
            return (f"ERROR: synthesized OK to {result.path} but Telegram "
                    f"send failed: {type(e).__name__}: {e}")

    # ── CLI / unknown surface: just report the path ──────────────────
    return f"OK: wrote {result.path} ({result.format}, {result.duration_ms/1000:.1f}s)"


# ── 8. vision_analyze — describe an image ────────────────────────────────────


async def _vision_analyze(args: dict[str, Any]) -> str:
    """Send an image to the configured vision LLM (cfg.auxiliary.vision)
    and return its description. Accepts a local path OR an http(s) URL.

    Smart default: if neither image_path nor image_url is supplied, OR the
    supplied path doesn't exist, this falls back to the MOST RECENTLY
    SAVED image in ~/argus-workspace/ (typically a Telegram photo named
    tg_image_*.jpg). This means the user can say "what do you see in
    this image" right after sending a photo and the agent can call
    vision_analyze() with no args.
    """
    from argus import config as _config

    image_path = (args.get("image_path") or "").strip()
    image_url  = (args.get("image_url")  or "").strip()
    prompt = (args.get("prompt") or "Describe this image in detail. "
              "List any text visible. Note objects, scene, and notable "
              "features.")

    # ── Smart fallback: most recent workspace image ─────────────────
    if not image_path and not image_url:
        ws = Path.home() / "argus-workspace"
        if ws.exists():
            candidates: list[Path] = []
            for pat in ("tg_image_*", "*.png", "*.jpg", "*.jpeg",
                        "*.gif", "*.webp", "*.bmp"):
                candidates.extend(ws.glob(pat))
            candidates = [p for p in candidates if p.is_file()]
            if candidates:
                latest = max(candidates, key=lambda p: p.stat().st_mtime)
                image_path = str(latest)
                # Annotate so the agent's reply can mention which image
                # we picked — useful when the user has uploaded several.
                args["_resolved_path"] = image_path
        if not image_path:
            return ("ERROR: no image_path or image_url given, and no recent "
                    "images found in ~/argus-workspace. Ask the user to "
                    "attach an image first.")

    cfg = _config.load()
    vp = cfg.auxiliary.vision

    # Load image bytes
    img_b64 = None
    mime    = "image/png"
    if image_path:
        p = Path(image_path).expanduser()
        if not p.exists():
            # Maybe a workspace-relative path?
            p = _safe_path(image_path) or p
        if not p.exists():
            return f"ERROR: image not found — {image_path}"
        ext = p.suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "gif": "image/gif",
                "webp": "image/webp", "bmp": "image/bmp"}.get(ext, "image/png")
        img_b64 = base64.b64encode(p.read_bytes()).decode()

    # Build OpenAI-shape content (works for Groq, OpenAI, OpenRouter, Anthropic-translated)
    image_url_part = (
        {"url": f"data:{mime};base64,{img_b64}"} if img_b64
        else {"url": image_url}
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": image_url_part},
        ],
    }]

    # Vision API needs multimodal `content` as a LIST (text + image_url
    # parts). Our standard transport flattens content to a string, so we
    # bypass it and POST directly. Works for any OpenAI-compatible vision
    # endpoint.
    from argus.data.providers import get_provider as _get_pinfo
    import httpx as _httpx

    # ── Smart provider selection — prefer whichever the user has a key for,
    #    in vision-quality order. Skips providers with no key set so we
    #    never hit "401 — Bearer " errors.
    # (provider_id, vision_model)
    _VISION_PREFERENCES = [
        # 1. Explicit config (only if its key exists)
        (vp.provider, vp.model or ""),
        # 2. Common cloud vendors, ranked by typical key availability
        ("openai",    "gpt-4o-mini"),
        ("openrouter","openai/gpt-4o-mini"),
        ("groq",      "meta-llama/llama-4-scout-17b-16e-instruct"),
        ("gemini",    "gemini-2.0-flash"),
        ("mistral",   "pixtral-large-latest"),
        ("anthropic", "claude-haiku-4-5"),
    ]

    chosen_provider = None
    chosen_model    = None
    chosen_info     = None
    chosen_key      = ""
    for pid, model in _VISION_PREFERENCES:
        if not pid:
            continue
        try:
            info = _get_pinfo(pid)
        except Exception:
            continue
        key = _config.resolve_secret(info.env_var, cfg) or ""
        if info.auth == "api_key" and not key:
            continue
        chosen_provider, chosen_model, chosen_info, chosen_key = pid, (model or info.models[0].id), info, key
        break

    if not chosen_info:
        return ("ERROR: no vision provider available. Add a key for any of: "
                "openai, openrouter, groq, gemini, mistral, anthropic.\n"
                "  e.g.  argus key openai sk-…")

    vision_provider_id = chosen_provider
    vision_model       = chosen_model
    info               = chosen_info
    api_key            = chosen_key

    # Compose the payload (OpenAI Vision spec — works on all the openai-compat
    # vendors). Anthropic-native would need translation; we add that later.
    payload = {
        "model": vision_model,
        "messages": [
            {"role": "system",
             "content": "You are a precise image describer. Be concrete; cite any visible text verbatim."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": image_url_part},
            ]},
        ],
        "max_tokens": 1024,
    }
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    url = info.base_url.rstrip("/") + "/chat/completions"
    try:
        async with _httpx.AsyncClient(timeout=cfg.vision.timeout) as c:
            r = await c.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            return (f"ERROR: vision API returned {r.status_code} from "
                    f"{vision_provider_id}: {r.text[:300]}")
        data = r.json()
        text = (data.get("choices", [{}])[0]
                    .get("message", {}).get("content", "") or "").strip()
        if args.get("_resolved_path"):
            text = f"(analysed: {args['_resolved_path']})\n\n{text}"
        return text or "(empty vision response)"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: vision call failed: {type(e).__name__}: {e}"


# ── Registration ─────────────────────────────────────────────────────────────


def register_hermes_ports() -> None:
    """Idempotent — safe to re-call. Registers all 8 ports under their
    Hermes-equivalent toolset names so existing prompts/skills transfer."""

    register(ToolImpl("files", ToolSpec(
        name="patch",
        description=(
            "Fuzzy find-and-replace inside one workspace file. "
            "old_text MUST be a unique substring of the current file body. "
            "If not unique, the call errors with the match count — supply "
            "more surrounding context. Use write_file for full overwrites."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Workspace-relative path"},
                "old_text":  {"type": "string", "description": "Unique substring to replace"},
                "new_text":  {"type": "string", "description": "Replacement text"},
            },
            "required": ["file_path", "old_text", "new_text"],
        },
    ), _patch))

    register(ToolImpl("files", ToolSpec(
        name="search_files",
        description=(
            "Search inside ~/argus-workspace by file name OR file content. "
            "Backed by ripgrep when available, with a pure-Python fallback. "
            "Returns up to 120 hits."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query":  {"type": "string"},
                "target": {"type": "string", "enum": ["content", "name"],
                           "description": "Default 'content'"},
                "path":   {"type": "string", "description": "Workspace subpath. Default '.'"},
            },
            "required": ["query"],
        },
    ), _search_files))

    register(ToolImpl("code", ToolSpec(
        name="execute_code",
        description=(
            "Run a Python script in a fresh subprocess inside the workspace. "
            "15-second default timeout. Returns exit code + stdout + stderr. "
            "Network is NOT blocked. Use this for ad-hoc computation, data "
            "munging, or any code-first answer."
        ),
        parameters={
            "type": "object",
            "properties": {
                "script":  {"type": "string", "description": "Python source"},
                "timeout": {"type": "number", "description": "Wall-clock seconds; default 15"},
            },
            "required": ["script"],
        },
    ), _execute_code))

    register(ToolImpl("todo", ToolSpec(
        name="todo",
        description=(
            "Session task list. Actions: list, add(text), complete(id), "
            "remove(id), clear. Persists to ~/.argus/todos.json."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "add", "complete", "remove", "clear"]},
                "text":   {"type": "string", "description": "For add"},
                "id":     {"type": "integer", "description": "For complete/remove"},
            },
            "required": ["action"],
        },
    ), _todo))

    register(ToolImpl("clarify", ToolSpec(
        name="clarify",
        description=(
            "Ask the user a single clarifying question and wait for their "
            "reply. Optionally provide up to 4 numbered choices. In "
            "non-interactive contexts (Telegram, cron) returns "
            "CLARIFY_UNAVAILABLE so you commit to a default."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "choices":  {"type": "array", "items": {"type": "string"},
                             "description": "Optional up-to-4 enumerated answers"},
            },
            "required": ["question"],
        },
    ), _clarify))

    register(ToolImpl("web", ToolSpec(
        name="web_extract",
        description=(
            "Fetch a URL and return clean Markdown content. Handles HTML "
            "(strips nav/script/style) and PDF (uses pypdf if installed). "
            "Truncates to ~8 KB. Prefer this over web_fetch for human-readable extraction."
        ),
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    ), _web_extract))

    register(ToolImpl("voice", ToolSpec(
        name="text_to_speech",
        description=(
            "Synthesise speech and write it to a temp file. Defaults to the "
            "TTS provider in config (Supertonic). Output formats: ogg "
            "(Telegram voice-bubble), mp3, wav. Returns the absolute file "
            "path for the caller to ship."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text":     {"type": "string"},
                "provider": {"type": "string",
                             "description": "supertonic|edge|openai|elevenlabs|piper"},
                "voice":    {"type": "string"},
                "format":   {"type": "string", "enum": ["ogg", "mp3", "wav"]},
            },
            "required": ["text"],
        },
    ), _text_to_speech))

    register(ToolImpl("vision", ToolSpec(
        name="vision_analyze",
        description=(
            "Describe an image using the configured vision LLM "
            "(cfg.auxiliary.vision). Accepts a local file path OR an "
            "http(s) URL. Returns the model's full description."
        ),
        parameters={
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Local file path (preferred when available)"},
                "image_url":  {"type": "string", "description": "Public URL"},
                "prompt":     {"type": "string", "description": "What to ask the vision model"},
            },
            "required": [],
        },
    ), _vision_analyze))
