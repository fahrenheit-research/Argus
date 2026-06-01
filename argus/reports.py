"""Formatted status reports for ARGUS — used by:
  • the chat REPL when the user types `Argus Report` / `/report`
  • the Telegram gateway's `/report` command
  • `argus report` CLI command

One report function, three output formats:
  - "terminal"  → Rich-renderable Markdown for the chat REPL
  - "telegram"  → plain Markdown (no Rich) for Telegram parse_mode=MARKDOWN
  - "plain"     → no styling, for logs / CI

Covers:
  • ARGUS — provider, model, toolsets, voice, vision, connectors, gateway, uptime
  • AgentBrain — typed knowledge graph entity + relation counts, recent additions
  • AgentMomento — skill router stats, top-cited skills, recent recordings
  • AgentWire — message envelope stats (sessions, average tokens, last activity)
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from argus import config as _config, paths


# ── Entry ────────────────────────────────────────────────────────────────────


async def build_status_report(cfg: _config.Config, *,
                              format: str = "telegram") -> str:
    """Compose a full status report.

    `format`:
      • "telegram" — Markdown safe for Telegram parse_mode=MARKDOWN
      • "terminal" — Rich-friendly Markdown
      • "plain"    — no styling
    """
    sections = [
        await _argus_section(cfg, format),
        await _connectors_section(cfg, format),
        _agentbrain_section(format),
        _agentmomento_section(format),
        _agentwire_section(format),
    ]
    header = _h1("⟨◇⟩  ARGUS  REPORT", format)
    footer = _dim(
        f"_Generated {time.strftime('%Y-%m-%d %H:%M:%S')}_  ·  "
        f"_Fahrenheit Research · f-r.co_",
        format,
    )
    return "\n\n".join([header, *sections, footer])


# ── ARGUS section ────────────────────────────────────────────────────────────


async def _argus_section(cfg: _config.Config, fmt: str) -> str:
    from argus.data.providers import get_provider
    info = get_provider(cfg.agent.default_provider)
    has_key = _config.has_secret(info.env_var, cfg)

    # TTS provider readiness check
    tts_ok, tts_detail = False, ""
    try:
        from argus.voice import available as _voice_available
        tts_ok, tts_detail = _voice_available(cfg.voice.tts_provider)
    except Exception:
        tts_detail = "voice subsystem not importable"

    # Tool count
    tool_count = "?"
    try:
        from argus.tools.registry import register_defaults, available_tools
        register_defaults()
        tool_count = str(len(available_tools()))
    except Exception:
        pass

    # Telegram gateway status
    tg_status = "off (not enabled)"
    if cfg.gateway.telegram.enabled:
        pid_path = paths.TELEGRAM_PID
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                if _pid_alive(pid):
                    tg_status = f"✓ running (PID {pid})"
                else:
                    tg_status = "stale PID — gateway crashed or was killed"
            except ValueError:
                tg_status = "PID file unreadable"
        else:
            tg_status = "enabled but not running"

    # ffmpeg presence
    import shutil
    ffmpeg = "✓ present" if shutil.which("ffmpeg") else "missing (TTS falls back to MP3 via lameenc)"

    rows = [
        ("Provider",       f"`{info.label}` " + ("✓ key set" if has_key else "✗ NO KEY")),
        ("Model",          f"`{cfg.agent.default_model}`"),
        ("Toolsets",       f"`{', '.join(cfg.agent.toolsets)}`"),
        ("Tools loaded",   f"`{tool_count}` registered"),
        ("Voice STT",      f"`{cfg.voice.stt_provider}/{cfg.voice.stt_model}`"),
        ("Voice TTS",      f"`{cfg.voice.tts_provider}` — " +
                            ("✓ ready" if tts_ok else f"✗ {tts_detail}")),
        ("Voice reply mode", f"`{cfg.voice.tts_mode}`"),
        ("Vision",         f"`{cfg.auxiliary.vision.provider}/{cfg.auxiliary.vision.model}`"),
        ("ffmpeg",         ffmpeg),
        ("Telegram gateway", tg_status),
    ]
    return _section("CORE", rows, fmt)


# ── Connectors section ──────────────────────────────────────────────────────


async def _connectors_section(cfg: _config.Config, fmt: str) -> str:
    rows: list[tuple[str, str]] = []
    try:
        from argus.connectors.registry import list_connectors
        for c in list_connectors():
            try:
                st = c.status()
                if st.connected:
                    val = f"✓ {st.account or 'connected'}"
                else:
                    val = "—"
            except Exception:
                val = "?"
            rows.append((f"{getattr(c, 'icon', '◇')} {c.name}", val))
    except Exception as e:
        rows.append(("connectors", f"could not enumerate: {e}"))
    return _section("CONNECTORS", rows, fmt)


# ── AgentBrain section (typed knowledge graph) ───────────────────────────────


def _agentbrain_section(fmt: str) -> str:
    """AgentBrain stats — entity + relation counts (in-memory store)."""
    rows: list[tuple[str, str]] = []
    try:
        from argus.agentbrain.core import AgentBrain, MemoryTier
        # AgentBrain is currently in-memory per process. We can still report
        # what its class can hold + show MemoryTier names so the user sees
        # what's wired even when counts are 0 in a fresh process.
        brain = AgentBrain()
        ent_count = len(getattr(brain, "entities", {}))
        rel_count = len(getattr(brain, "relations", {}))
        tiers = [getattr(MemoryTier, n) for n in dir(MemoryTier)
                 if not n.startswith("_") and isinstance(getattr(MemoryTier, n), str)]
        rows.append(("Backend",   "in-memory (ephemeral per process)"))
        rows.append(("Memory tiers", ", ".join(tiers) if tiers else "—"))
        rows.append(("Entities",  f"`{ent_count}` (this process)"))
        rows.append(("Relations", f"`{rel_count}` (this process)"))
        rows.append(("Synthesis", "✓ engine attached" if getattr(brain, "synthesis_engine", None) else "—"))
        rows.append(("Hermes export", "✓ via as_hermes_provider()"))
    except Exception as e:
        rows.append(("Status", f"unavailable: {type(e).__name__}: {e}"[:80]))
    return _section("AGENT  BRAIN  ·  typed knowledge graph", rows, fmt)


# ── AgentMomento section (skill router) ──────────────────────────────────────


def _agentmomento_section(fmt: str) -> str:
    """AgentMomento stats — skills indexed + top-used skills from .index.json."""
    rows: list[tuple[str, str]] = []
    try:
        from argus.agentmomento.skill_router import get_router, SKILLS_DIR, INDEX_PATH, CATEGORIES
        router = get_router()
        rows.append(("Skills dir", f"`{SKILLS_DIR}`"))
        rows.append(("Categories", f"`{len(CATEGORIES)}`"))
        if Path(INDEX_PATH).exists():
            import json as _j
            idx = _j.loads(Path(INDEX_PATH).read_text())
            rows.append(("Indexed skills", f"`{len(idx)}`"))
            # Top by uses (skills have 'uses' counter from update_skill_stats)
            sortable = [(name, data.get("uses", 0)) for name, data in idx.items()
                        if isinstance(data, dict)]
            top = sorted(sortable, key=lambda x: -x[1])[:3]
            if top and any(u > 0 for _, u in top):
                rows.append(("Top used",
                             ", ".join(f"{n} ({u})" for n, u in top if u > 0)))
            elif idx:
                first3 = list(idx.keys())[:3]
                rows.append(("Loaded", ", ".join(first3)))
        else:
            rows.append(("Index", "not yet built — first call will create it"))
    except Exception as e:
        rows.append(("Status", f"unavailable: {type(e).__name__}: {e}"[:80]))
    return _section("AGENT  MOMENTO  ·  BM25 skill router", rows, fmt)


# ── AgentWire section (message envelopes) ────────────────────────────────────


def _agentwire_section(fmt: str) -> str:
    """AgentWire stats — envelope classes loaded + session log size."""
    rows: list[tuple[str, str]] = []
    try:
        from argus.agentwire.envelope import MessageEnvelope, ErrorEnvelope
        from argus.agentwire import encoder, decoder
        rows.append(("Envelopes",  "MessageEnvelope, ErrorEnvelope"))
        rows.append(("Encoder",    "✓ loaded" if encoder else "✗"))
        rows.append(("Decoder",    "✓ loaded" if decoder else "✗"))
        # Sessions on disk
        if paths.SESSIONS_DIR.exists():
            session_files = list(paths.SESSIONS_DIR.glob("*.json")) + \
                            list(paths.SESSIONS_DIR.glob("*.jsonl"))
            rows.append(("Sessions on disk", f"`{len(session_files)}`"))
            if session_files:
                latest = max(session_files, key=lambda p: p.stat().st_mtime)
                rows.append(("Latest session", f"`{latest.name}`"))
        else:
            rows.append(("Sessions",  "no session dir yet (first chat will create)"))
    except Exception as e:
        rows.append(("Status", f"unavailable: {type(e).__name__}: {e}"[:80]))
    return _section("AGENT  WIRE  ·  message envelopes", rows, fmt)


# ── Formatting helpers ──────────────────────────────────────────────────────


def _section(title: str, rows: list[tuple[str, str]], fmt: str) -> str:
    """Render a labelled section as a 2-col table-ish layout."""
    if fmt == "telegram":
        # Telegram Markdown is fussy — use simple bullets, no tables.
        head = f"*── {title} ──*"
        lines = [head]
        for label, value in rows:
            lines.append(f"  • *{label}*: {value}")
        return "\n".join(lines)
    elif fmt == "terminal":
        head = f"### ── {title} ──"
        lines = [head, ""]
        for label, value in rows:
            lines.append(f"  - **{label}**: {value}")
        return "\n".join(lines)
    else:  # plain
        head = f"-- {title} --"
        lines = [head]
        for label, value in rows:
            v_clean = value.replace("*", "").replace("`", "")
            lines.append(f"  {label:20}  {v_clean}")
        return "\n".join(lines)


def _h1(text: str, fmt: str) -> str:
    if fmt == "telegram": return f"*{text}*"
    if fmt == "terminal": return f"# {text}"
    return text + "\n" + "=" * len(text)


def _dim(text: str, fmt: str) -> str:
    if fmt == "telegram": return f"_{text.strip('_')}_"
    if fmt == "terminal": return text
    return text.replace("_", "").replace("*", "").replace("`", "")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
