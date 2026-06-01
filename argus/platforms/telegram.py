"""ARGUS Telegram gateway — production-grade, fully async.

Media capabilities:
  • Text messages   → agent loop (streaming edits, animated thinking indicator)
  • Voice notes     → Groq Whisper STT → transcribed text → agent
  • Photos          → vision model (OCR + description) OR save + describe
  • Documents/files → download → read text content → agent
  • Audio files     → Groq Whisper STT (same as voice)
  • Stickers/GIFs   → polite decline with explanation

Streaming UX (Hermes-inspired):
  • 👀 reaction on receive (thinking)
  • Animated placeholder: ⟨◇⟩ … · .. ·
  • Progressive edits every 400 ms once tokens arrive
  • ✓ reaction on success · ✗ on error
  • Tool-call lines shown inline: ⚡ `tool_name`…

Allow-list: every handler enforces allow_filter — unlisted users are
silently dropped and logged at WARN.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from pathlib import Path
from typing import Any

from telegram import Update, ReactionTypeEmoji
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from argus import config as _config
from argus import paths
from argus.loop import (
    Conversation,
    ErrorEvent,
    FinishEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    run_turn,
)

log = logging.getLogger("argus.telegram")

_MAX_MSG = 4000       # Telegram hard limit
_EDIT_THROTTLE_MS = 400   # faster than 600 for snappier feel


# ── Allow-list persistence ────────────────────────────────────────────────────


def parse_allow_list(raw: str | None) -> set[int]:
    if not raw:
        return set()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def add_user_to_allow_list(user_id: str) -> None:
    cfg = _config.load()
    env = dict(cfg.env)
    raw = env.get("TELEGRAM_ALLOWED_USERS", "")
    ids = {s.strip() for s in raw.split(",") if s.strip()}
    ids.add(user_id.strip())
    env["TELEGRAM_ALLOWED_USERS"] = ",".join(sorted(ids))
    _config.save_env(env)


def remove_user_from_allow_list(user_id: str) -> bool:
    cfg = _config.load()
    env = dict(cfg.env)
    raw = env.get("TELEGRAM_ALLOWED_USERS", "")
    ids = {s.strip() for s in raw.split(",") if s.strip()}
    if user_id.strip() not in ids:
        return False
    ids.discard(user_id.strip())
    env["TELEGRAM_ALLOWED_USERS"] = ",".join(sorted(ids))
    _config.save_env(env)
    return True


# ── Reaction + message helpers ────────────────────────────────────────────────


async def _react(update: Update, emoji: str) -> None:
    try:
        await update.message.set_reaction([ReactionTypeEmoji(emoji=emoji)])
    except (BadRequest, TelegramError, Exception):
        pass


async def _safe_edit(msg: Any, text: str, parse_mode: str | None = ParseMode.MARKDOWN) -> None:
    text = text[:_MAX_MSG]
    if not text.strip():
        return
    for mode in ([parse_mode] if parse_mode else []) + [None]:
        try:
            kwargs: dict = {"text": text}
            if mode:
                kwargs["parse_mode"] = mode
            await msg.edit_text(**kwargs)
            return
        except BadRequest:
            if mode is None:
                return


def _build_display(tool_lines: list[str], text_parts: list[str], thinking: bool = False) -> str:
    parts: list[str] = []
    if tool_lines:
        parts.append("\n".join(tool_lines))
    reply = "".join(text_parts).strip()
    if reply:
        parts.append(reply)
    if not parts:
        if thinking:
            return "⟨◇⟩ …"
        return "…"
    return "\n\n".join(parts)


# ── Animated thinking placeholder ─────────────────────────────────────────────
#
# Two-layer animation:
#   1. Glyph spinner   — diamond glyph rotates frame-by-frame every 600ms
#   2. Witty subtitle  — short verb-phrase rotates every ~2.4s
# The subtitle pool is biased so common cases pull repeatable phrases; rare
# moods spice things up. Designed to feel like an old text adventure boot.


_GLYPH_SPIN = ["⟨◇⟩", "⟨◈⟩", "⟨◆⟩", "⟨◈⟩"]

# Curated witty progress phrases. Hand-tuned to feel like ARGUS *thinking*,
# not corporate "loading…". Roughly 40 phrases; each <30 chars.
_THINK_PHRASES: tuple[str, ...] = (
    "consulting the constellation",
    "scanning the perimeter",
    "calibrating sentinels",
    "tessellating thoughts",
    "polarizing reality",
    "drafting a sharper answer",
    "checking the mast",
    "unfolding the map",
    "quenching plasma",
    "threading the lattice",
    "decoding entropy",
    "stitching meridians",
    "tracing the signal",
    "spinning the gyros",
    "convening the daemons",
    "lighting the watchfires",
    "sharpening the glyphs",
    "phasing in",
    "anchoring the mast",
    "drawing the wards",
    "stoking the forge",
    "bottling lightning",
    "distilling intent",
    "whetting edges",
    "priming oracles",
    "auditing the ether",
    "rolling the dice",
    "humming to the data",
    "tuning the oscillators",
    "interrogating the cache",
    "weighing the evidence",
    "stretching the canvas",
    "shaping the response",
    "unfurling vectors",
    "loading the long answer",
    "lining up the receipts",
    "checking the rear-view",
    "warming the magnetrons",
    "drafting a witty reply",
    "marshaling the answer",
)

# Tool-aware phrases. When a specific tool is firing, swap in a phrase that
# *hints* at what's happening — keeps the user oriented without giving up
# the magic. Key is the tool name; value is the action verb-phrase.
_TOOL_PHRASES: dict[str, tuple[str, ...]] = {
    "web_search":  ("searching the open web", "casting a wide net", "trawling the index"),
    "web_fetch":   ("fetching the page", "pulling the document"),
    "web_extract": ("reading the page", "extracting the meat"),
    "read_file":   ("opening the file", "scanning lines"),
    "write_file":  ("committing to disk", "writing it down"),
    "patch":       ("applying the patch", "threading the needle"),
    "search_files":("ripgrepping the workspace", "combing the files"),
    "execute_code":("running the script", "spinning up Python"),
    "memory":      ("rifling through memory", "checking the ledger"),
    "todo":        ("updating the kanban", "marking the board"),
    "calculator":  ("crunching the numbers",),
    "get_datetime":("checking the chronometer",),
    "vision_analyze": ("studying the image", "training the eye"),
    "text_to_speech": ("warming the vocal cords", "tuning the voice"),
    "orchestrate": ("convening the constellation", "summoning specialists"),
    "delegate_task": ("dispatching a scout", "sending a runner"),
    "setup_api_from_docs": ("reading the docs", "autodetecting auth"),
    "run_command": ("preparing the shell",),
    "graph":       ("walking the knowledge graph",),
}


def _pick_phrase(tool_name: str | None = None) -> str:
    """Choose a witty progress phrase. If a tool is firing, prefer tool-aware."""
    import random
    if tool_name and tool_name in _TOOL_PHRASES:
        return random.choice(_TOOL_PHRASES[tool_name])
    return random.choice(_THINK_PHRASES)


async def _animate_thinking(msg: Any, stop_event: asyncio.Event,
                            active_tool: dict[str, str | None]) -> None:
    """Spin the glyph every 600ms; rotate the witty subtitle every ~2.4s.

    `active_tool` is a shared mutable dict {"name": <tool_name|None>} that the
    caller updates when a ToolCallEvent fires, so the subtitle becomes
    tool-aware in real time.
    """
    spin = 0
    phrase = _pick_phrase(active_tool.get("name"))
    last_phrase_swap = 0
    last_tool_seen: str | None = active_tool.get("name")
    while not stop_event.is_set():
        glyph = _GLYPH_SPIN[spin % len(_GLYPH_SPIN)]
        # Swap the phrase when the tool changes, or every 4 spin frames (~2.4s)
        current_tool = active_tool.get("name")
        if current_tool != last_tool_seen or spin - last_phrase_swap >= 4:
            phrase = _pick_phrase(current_tool)
            last_phrase_swap = spin
            last_tool_seen = current_tool
        frame = f"{glyph}  _{phrase}…_"
        await _safe_edit(msg, frame, parse_mode=ParseMode.MARKDOWN)
        spin += 1
        try:
            await asyncio.wait_for(asyncio.shield(stop_event.wait()), timeout=0.6)
        except asyncio.TimeoutError:
            pass


def _initial_think_frame() -> str:
    """First frame shown on message arrival — picked fresh per request."""
    import random
    phrase = random.choice(_THINK_PHRASES)
    return f"⟨◇⟩  _{phrase}…_"


# ── Voice / audio transcription (Groq Whisper) ────────────────────────────────


async def _transcribe_voice(audio_bytes: bytes, cfg: _config.Config) -> str | None:
    """Transcribe voice/audio using Groq Whisper-large-v3.

    Returns the transcript string, or None if transcription fails or
    no STT provider is configured.
    """
    api_key = _config.resolve_secret("GROQ_API_KEY", cfg)
    if not api_key:
        return None
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=api_key)
        # Groq expects the file as (filename, bytes-io, content-type)
        audio_io = io.BytesIO(audio_bytes)
        resp = await client.audio.transcriptions.create(
            file=("audio.ogg", audio_io, "audio/ogg"),
            model=cfg.voice.stt_model,           # whisper-large-v3
            response_format="text",
            language="en",
        )
        return str(resp).strip() if resp else None
    except Exception as e:
        log.warning("Whisper transcription failed: %s", e)
        return None


# ── Vision / OCR (image understanding) ────────────────────────────────────────


async def _describe_image(image_bytes: bytes, cfg: _config.Config, prompt: str = "") -> str:
    """Send an image to a vision-capable model and return the description.

    Tries: OpenAI gpt-4o-mini → Anthropic claude-haiku → graceful fallback.
    """
    import base64

    b64 = base64.b64encode(image_bytes).decode()
    user_prompt = prompt or "Describe this image in detail. If it contains text, extract it verbatim (OCR)."

    # Try OpenAI gpt-4o-mini (vision)
    openai_key = _config.resolve_secret("OPENAI_API_KEY", cfg)
    if openai_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=openai_key)
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text",       "text": user_prompt},
                        {"type": "image_url",  "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }],
            )
            return resp.choices[0].message.content or "(no description)"
        except Exception as e:
            log.debug("OpenAI vision failed: %s", e)

    # Try Anthropic claude-haiku (vision)
    anthropic_key = _config.resolve_secret("ANTHROPIC_API_KEY", cfg)
    if anthropic_key:
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=anthropic_key)
            resp = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text",  "text": user_prompt},
                    ],
                }],
            )
            return resp.content[0].text if resp.content else "(no description)"
        except Exception as e:
            log.debug("Anthropic vision failed: %s", e)

    # Try Groq llama-3.2-11b-vision-preview if available
    groq_key = _config.resolve_secret("GROQ_API_KEY", cfg)
    if groq_key:
        try:
            from groq import AsyncGroq
            client = AsyncGroq(api_key=groq_key)
            resp = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text",      "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }],
            )
            return resp.choices[0].message.content or "(no description)"
        except Exception as e:
            log.debug("Groq vision failed: %s", e)

    return "(vision model not available — add an OpenAI or Anthropic key to enable image understanding)"


# ── Document / file reading ───────────────────────────────────────────────────


def _read_document(file_bytes: bytes, filename: str) -> str:
    """Extract text from a downloaded Telegram document."""
    fname = filename.lower()

    # Plain text
    if any(fname.endswith(ext) for ext in (".txt", ".md", ".py", ".js", ".ts",
                                            ".json", ".yaml", ".yml", ".toml",
                                            ".sh", ".bash", ".csv", ".log")):
        try:
            return file_bytes.decode("utf-8", errors="replace")[:8000]
        except Exception:
            return "(could not decode text file)"

    # PDF — basic text extraction
    if fname.endswith(".pdf"):
        try:
            import re
            # Simple PDF text extraction without extra deps
            text = file_bytes.decode("latin-1", errors="replace")
            # Extract text between BT...ET blocks
            parts = re.findall(r"BT\s*(.*?)\s*ET", text, re.S)
            words: list[str] = []
            for part in parts:
                tokens = re.findall(r"\(([^)]+)\)", part)
                words.extend(tokens)
            result = " ".join(words)
            return result[:6000] if result else "(PDF — no readable text found, try a vision model)"
        except Exception:
            return "(could not extract text from PDF)"

    return f"(received file: {filename}, {len(file_bytes):,} bytes — binary format not supported for text extraction)"


# ── Core message handler ──────────────────────────────────────────────────────


async def _handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
    *,
    attachment_note: str = "",
    is_voice_input: bool = False,
) -> None:
    """Run one turn through the agent loop with streaming Telegram edits.

    If `is_voice_input=True` (the user sent a voice note), and
    cfg.voice.tts_mode is `voice_only` or `all`, we also TTS the final
    text reply and send it as a voice bubble.
    """
    cfg: _config.Config = context.bot_data["cfg"]
    convos: dict[int, Conversation] = context.bot_data["convos"]

    chat_id = update.effective_chat.id
    convo = convos.setdefault(chat_id, Conversation())

    # Pin the platform context so tools (text_to_speech, send_photo, etc.)
    # can deliver artifacts straight to this chat instead of writing to disk.
    from argus.platform_context import set_platform_context, PlatformContext
    set_platform_context(PlatformContext(
        platform="telegram",
        bot=context.bot,
        chat_id=chat_id,
        user_id=update.effective_user.id if update.effective_user else None,
        is_voice_input=is_voice_input,
    ))

    if cfg.gateway.telegram.reactions:
        await _react(update, "👀")

    # Combine attachment context with user message
    full_text = user_text
    if attachment_note:
        full_text = f"{attachment_note}\n\n{user_text}" if user_text.strip() else attachment_note

    # ── Auto-swarm detection ──────────────────────────────────────────────
    # If the prompt contains multiple distinct tasks, immediately fan out to
    # the Constellation instead of the single-turn agent. No user command
    # needed — ARGUS detects complexity and swarms automatically.
    try:
        from argus.swarm.auto import should_swarm
        routable, confidence, swarm_reason = should_swarm(full_text)
        if routable:
            log.info("auto-swarm triggered (%.0f%% confidence): %s",
                     confidence * 100, swarm_reason)
            await _safe_edit(
                placeholder if 'placeholder' in dir() else None,
                f"⟨◆⟩  _Constellation activated — {swarm_reason}_",
                parse_mode=ParseMode.MARKDOWN,
            ) if False else None  # placeholder not created yet, handled below
    except Exception:
        routable = False

    # Send the animated thinking placeholder (needed before the reply)
    try:
        if routable:
            placeholder = await update.message.reply_text(
                f"⟨◆⟩  _Swarm activated ({confidence:.0%}) — fanning out to specialists…_",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            placeholder = await update.message.reply_text(
                _initial_think_frame(), parse_mode=ParseMode.MARKDOWN,
            )
    except TelegramError as e:
        log.error("failed to send placeholder: %s", e)
        return

    # ── Constellation path ─────────────────────────────────────────────────
    if routable:
        try:
            from argus.swarm import Constellation
            constellation = Constellation(
                goal=full_text, cfg=cfg,
                budget_tokens=30_000, timeout_seconds=180,
            )
            async for evt in constellation.stream():
                from argus.swarm.events import BudgetTick, RoleStarted, RoleFinished
                if isinstance(evt, BudgetTick):
                    pct = int(evt.spent_tokens / max(1, evt.total_tokens) * 100)
                    await _safe_edit(
                        placeholder,
                        f"⟨◆⟩  _Constellation running — {pct}% budget · "
                        f"{evt.active_roles} active role(s) · {evt.elapsed_ms // 1000}s_",
                        parse_mode=ParseMode.MARKDOWN,
                    )
            result = constellation.result
            if cfg.gateway.telegram.reactions:
                await _react(update, "✓")
            from argus.render import render_for_telegram
            final_text = render_for_telegram(result.answer)
            stats = (f"\n\n_⟨◇⟩ Constellation: {result.role_count} roles · "
                     f"{len(result.confirmed_outputs)} verified · "
                     f"{result.elapsed_ms // 1000}s_")
            await _safe_edit(placeholder, (final_text + stats)[:4090],
                             parse_mode=ParseMode.MARKDOWN)
            return
        except Exception as e:
            log.error("auto-swarm failed, falling back to normal turn: %s", e)
            await _safe_edit(placeholder, _initial_think_frame(),
                             parse_mode=ParseMode.MARKDOWN)

    # ── Normal single-turn path ────────────────────────────────────────────
    # Start thinking animation in parallel — shared mutable so tool calls
    # change the witty subtitle in real time.
    stop_anim = asyncio.Event()
    active_tool: dict[str, str | None] = {"name": None}
    anim_task = asyncio.create_task(_animate_thinking(placeholder, stop_anim, active_tool))

    text_parts:  list[str] = []
    tool_lines:  list[str] = []
    last_edit_at = 0.0
    had_error    = False
    got_first_token = False

    try:
        async for evt in run_turn(cfg, convo, full_text):
            if isinstance(evt, TextEvent):
                if not got_first_token:
                    # Stop animation, switch to real content
                    stop_anim.set()
                    await asyncio.sleep(0.1)   # let animation clean up
                    got_first_token = True
                text_parts.append(evt.text)

            elif isinstance(evt, ToolCallEvent):
                name = evt.name
                active_tool["name"] = name   # ← drives the witty subtitle
                args = list((evt.arguments or {}).items())[:2]
                args_str = ", ".join(f"{k}={v!r}" for k, v in args)
                if args_str:
                    tool_lines.append(f"⚡ `{name}({args_str})`…")
                else:
                    tool_lines.append(f"⚡ `{name}`…")

            elif isinstance(evt, ToolResultEvent):
                active_tool["name"] = None   # cleared until next tool fires
                if tool_lines:
                    tool_lines[-1] = tool_lines[-1].rstrip("…") + f" ✓ {evt.elapsed_ms/1000:.1f}s"

            elif isinstance(evt, ErrorEvent):
                had_error = True
                text_parts.append(f"\n⚠️ {evt.message}")

            # Throttled edit: only after first real token
            if got_first_token:
                now = time.monotonic() * 1000
                if now - last_edit_at >= _EDIT_THROTTLE_MS:
                    display = _build_display(tool_lines, text_parts)
                    await _safe_edit(placeholder, display)
                    last_edit_at = now

    except Exception as e:
        log.error("agent loop error in telegram handler: %s", e, exc_info=True)
        had_error = True
        text_parts.append(f"\n⚠️ Error: {type(e).__name__}: {e}")

    finally:
        stop_anim.set()
        await asyncio.sleep(0.05)
        anim_task.cancel()

    # Final edit — always clean-up with the complete response.
    # render_for_telegram strips em-dashes, converts `**bold**` → `*bold*`,
    # turns `# Header` → `*Header*`, and normalises bullets. The result is
    # safe for parse_mode=MARKDOWN — no raw `**` or `—` surfaces in chat.
    from argus.render import render_for_telegram
    cleaned_parts = [render_for_telegram(p) for p in text_parts]
    final = _build_display(tool_lines, cleaned_parts)
    await _safe_edit(placeholder, final or "_(no response)_")

    if cfg.gateway.telegram.reactions:
        await _react(update, "✗" if had_error else "✓")

    # ── TTS reply (voice bubble) ──────────────────────────────────────
    should_voice = (not had_error) and bool("".join(text_parts).strip()) and (
        cfg.voice.tts_mode == "all"
        or (cfg.voice.tts_mode == "voice_only" and is_voice_input)
    )
    if should_voice and cfg.voice.tts_provider != "none":
        try:
            from argus.voice import synthesize
            import shutil as _sh
            # Telegram contract: voice bubbles must be OGG/Opus. We can only
            # produce that when ffmpeg is installed. Otherwise we ALWAYS
            # downgrade to MP3 — never WAV — so the user sees a proper audio
            # bubble in the chat instead of a raw file attachment.
            preferred = "ogg" if _sh.which("ffmpeg") else "mp3"
            result = await synthesize("".join(text_parts), cfg=cfg,
                                       output_format=preferred)
            if result and result.path.exists():
                with result.path.open("rb") as fh:
                    if result.format == "ogg":
                        await context.bot.send_voice(
                            chat_id=update.effective_chat.id, voice=fh,
                            duration=int(result.duration_ms / 1000) or None,
                        )
                    else:
                        # MP3 (or any non-OGG) → audio bubble. Never WAV.
                        await context.bot.send_audio(
                            chat_id=update.effective_chat.id, audio=fh,
                            duration=int(result.duration_ms / 1000) or None,
                            title="ARGUS",
                        )
        except Exception as e:  # noqa: BLE001
            log.warning("TTS reply failed: %s", e)


# ── Message-type handlers ─────────────────────────────────────────────────────


async def _on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        return
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    except TelegramError:
        pass
    await _handle_message(update, context, text)


async def _on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Voice note → Groq Whisper → transcribed text → agent."""
    cfg: _config.Config = context.bot_data["cfg"]

    if not cfg.gateway.telegram.forward_voice:
        await update.message.reply_text("(voice forwarding is disabled in config)")
        return

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.RECORD_VOICE
        )
    except TelegramError:
        pass

    # Download voice note
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    placeholder = await update.message.reply_text("🎙 Transcribing…")
    try:
        voice_file = await context.bot.get_file(voice.file_id)
        buf = io.BytesIO()
        await voice_file.download_to_memory(buf)
        audio_bytes = buf.getvalue()
    except Exception as e:
        await placeholder.edit_text(f"⚠️ Could not download voice note: {e}")
        return

    transcript = await _transcribe_voice(audio_bytes, cfg)
    if not transcript:
        await placeholder.edit_text(
            "🎙 Voice transcription unavailable.\n"
            "Add a Groq API key to enable it:  `argus key groq gsk_...`"
        )
        return

    await placeholder.edit_text(f'🎙 _"{transcript}"_', parse_mode=ParseMode.MARKDOWN)
    await _handle_message(update, context, transcript, is_voice_input=True)


async def _on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Photo → vision model → description + OCR → agent."""
    cfg: _config.Config = context.bot_data["cfg"]

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
    except TelegramError:
        pass

    placeholder = await update.message.reply_text("🖼 Analysing image…")

    # Get the largest photo variant
    photo = update.message.photo[-1]
    try:
        photo_file = await context.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await photo_file.download_to_memory(buf)
        image_bytes = buf.getvalue()
    except Exception as e:
        await placeholder.edit_text(f"⚠️ Could not download image: {e}")
        return

    caption = (update.message.caption or "").strip()
    user_prompt = caption if caption else ""

    description = await _describe_image(image_bytes, cfg, prompt=user_prompt or "")

    # Save image to workspace for reference
    ws = Path.home() / "argus-workspace"
    ws.mkdir(exist_ok=True)
    img_path = ws / f"tg_image_{photo.file_id[:8]}.jpg"
    img_path.write_bytes(image_bytes)

    attachment_note = (
        f"[User sent a photo. Vision analysis:]\n{description}\n"
        f"[Saved to: ~/argus-workspace/{img_path.name}]"
    )
    await placeholder.delete()
    await _handle_message(update, context, caption or "What do you see in this image?",
                          attachment_note=attachment_note)


async def _on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Document/file → download → read text → agent."""
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT
        )
    except TelegramError:
        pass

    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or f"file_{doc.file_id[:8]}"
    placeholder = await update.message.reply_text(f"📄 Reading {filename}…")

    try:
        doc_file = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await doc_file.download_to_memory(buf)
        file_bytes = buf.getvalue()
    except Exception as e:
        await placeholder.edit_text(f"⚠️ Could not download {filename}: {e}")
        return

    # Save to workspace
    ws = Path.home() / "argus-workspace"
    ws.mkdir(exist_ok=True)
    save_path = ws / filename
    save_path.write_bytes(file_bytes)

    # Extract text if possible
    text_content = _read_document(file_bytes, filename)
    kb = len(file_bytes) / 1024

    caption = (update.message.caption or "").strip()
    attachment_note = (
        f"[User attached: {filename} ({kb:.1f} KB)]\n"
        f"[Saved to: ~/argus-workspace/{filename}]\n"
        f"[Content excerpt:]\n{text_content[:3000]}"
    )

    await placeholder.delete()
    await _handle_message(
        update, context,
        caption or f"Here is a file: {filename}",
        attachment_note=attachment_note,
    )


async def _on_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    emoji = getattr(update.message.sticker, "emoji", "🎭") or "🎭"
    await update.message.reply_text(f"{emoji} Nice sticker! Type a message to chat with me.")


# ── Command handlers ──────────────────────────────────────────────────────────


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⟨◇⟩ *ARGUS online*\n\n"
        "Send any message, photo, voice note, or file — I'll handle it.\n\n"
        "_Built by Fahrenheit Research · f-r.co_",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*ARGUS — your watchful agent*\n\n"
        "📝 Text → I'll answer using my tools\n"
        "🎙 Voice note → transcribed, answered, and replied with TTS\n"
        "📷 Photo → analysed with vision AI (OCR available)\n"
        "📄 File → read and answered\n\n"
        "*Commands:*\n"
        "`/start` — welcome panel\n"
        "`/help` — this message\n"
        "`/status` — provider, model, toolsets\n"
        "`/report` — full report (ARGUS + AgentBrain + Momento + Wire)\n"
        "`/clear` — clear this conversation\n"
        "`/clearmemory` — wipe MEMORY.md + USER.md (keeps API keys)\n"
        "`/voice off|voice_only|all` — toggle TTS replies\n"
        "`/connect` — how to link Gmail, Twitter, LinkedIn, …\n\n"
        "_Built by Fahrenheit Research · f-r.co_",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: _config.Config = context.bot_data["cfg"]
    from argus.data.providers import get_provider
    info = get_provider(cfg.agent.default_provider)
    has_key = _config.has_secret(info.env_var, cfg)
    convos: dict = context.bot_data["convos"]
    turns = len(convos.get(update.effective_chat.id, Conversation()).messages)
    await update.message.reply_text(
        f"*ARGUS Status*\n\n"
        f"Provider: `{info.label}` {'✓' if has_key else '✗'}\n"
        f"Model: `{cfg.agent.default_model}`\n"
        f"Toolsets: `{', '.join(cfg.agent.toolsets)}`\n"
        f"Conversation turns: `{turns}`\n"
        f"Voice STT: `{cfg.voice.stt_model if has_key else 'disabled'}`\n"
        f"Vision: `{'openai/gpt-4o-mini' if _config.has_secret('OPENAI_API_KEY', cfg) else 'groq vision' if has_key else 'not configured'}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["convos"].pop(update.effective_chat.id, None)
    await update.message.reply_text("✓ Conversation cleared. Starting fresh.")


async def _cmd_clear_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wipe ONLY MEMORY.md + USER.md — keeps API keys, OAuth tokens, connectors."""
    from argus import paths
    wiped = []
    for f in (paths.MEMORY_MD, paths.USER_MD):
        if f.exists():
            f.unlink()
            wiped.append(f.name)
    await update.message.reply_text(
        f"✓ Memory cleared ({', '.join(wiped) or 'nothing to wipe'}).\n\n"
        f"_API keys, OAuth tokens, and connectors are untouched._",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a formatted status report covering ARGUS + AgentBrain + AgentMomento + AgentWire."""
    from argus.reports import build_status_report
    cfg: _config.Config = context.bot_data["cfg"]
    report = await build_status_report(cfg, format="telegram")
    # Telegram caps messages at 4096 chars; trim if needed
    await update.message.reply_text(report[:4090], parse_mode=ParseMode.MARKDOWN)


async def _cmd_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle voice-reply mode (off | voice_only | all)."""
    cfg: _config.Config = context.bot_data["cfg"]
    arg = (context.args[0] if context.args else "").strip().lower()
    if arg not in ("off", "voice_only", "all"):
        await update.message.reply_text(
            "*Voice reply modes:*\n\n"
            f"Current: `{cfg.voice.tts_mode}`\n\n"
            "`/voice off`         — never reply with TTS\n"
            "`/voice voice_only`  — TTS only when user sent voice (default)\n"
            "`/voice all`         — TTS reply to every message",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    cfg.voice.tts_mode = arg
    _config.save_config(cfg)
    await update.message.reply_text(
        f"✓ Voice reply mode → `{arg}`", parse_mode=ParseMode.MARKDOWN
    )


async def _cmd_speak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/speak <text> — synthesise text to a voice bubble on demand."""
    text = " ".join(context.args or []).strip()
    if not text:
        await update.message.reply_text(
            "*Speak — text-to-voice on demand*\n\n"
            "Send: `/speak Hello from Argus, sentinel online.`\n\n"
            "Or just ask in plain English: \"send me a voice note saying …\"",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    cfg: _config.Config = context.bot_data["cfg"]
    if cfg.voice.tts_provider == "none":
        await update.message.reply_text(
            "TTS is disabled. Enable with: `/voice voice_only` or `/voice all`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    # Set the platform context for this single call, then synthesise+send.
    from argus.platform_context import set_platform_context, PlatformContext
    from argus.tools.hermes_ports import _text_to_speech
    set_platform_context(PlatformContext(
        platform="telegram", bot=context.bot,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id if update.effective_user else None,
        is_voice_input=False,
    ))
    placeholder = await update.message.reply_text("🎙 _synthesising…_", parse_mode=ParseMode.MARKDOWN)
    try:
        result = await _text_to_speech({"text": text})
        await placeholder.delete()
        if result.startswith("ERROR"):
            await update.message.reply_text(result)
    except Exception as e:  # noqa: BLE001
        await placeholder.edit_text(f"⚠️ TTS failed: {e}")


async def _cmd_brain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/brain — memory dashboard (compact, Telegram-friendly)."""
    from argus import paths
    user_kb  = paths.USER_MD.stat().st_size  / 1024 if paths.USER_MD.exists()  else 0
    mem_kb   = paths.MEMORY_MD.stat().st_size / 1024 if paths.MEMORY_MD.exists() else 0
    skills_n = 0
    try:
        import json as _j
        idx_path = paths.HOME / "skills" / ".index.json"
        if idx_path.exists():
            skills_n = len(_j.loads(idx_path.read_text()))
    except Exception:
        pass
    await update.message.reply_text(
        "*Memory dashboard*\n\n"
        f"USER.md      `{user_kb:.1f} KB / 32 KB`\n"
        f"MEMORY.md    `{mem_kb:.1f} KB / 32 KB`\n"
        f"Skills       `{skills_n}` indexed\n\n"
        "Use `/clearmemory` to wipe MEMORY.md + USER.md (keeps keys).",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/sessions — list recent chat sessions."""
    from argus import paths
    if not paths.SESSIONS_DIR.exists():
        await update.message.reply_text("No saved sessions yet.")
        return
    files = sorted(paths.SESSIONS_DIR.glob("*.json*"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    if not files:
        await update.message.reply_text("No saved sessions yet.")
        return
    import time as _t
    lines = ["*Recent sessions*\n"]
    for f in files:
        age = (_t.time() - f.stat().st_mtime) / 3600
        age_s = f"{age:.0f}h ago" if age < 48 else f"{age/24:.0f}d ago"
        lines.append(f"• `{f.stem[:24]}` — {age_s}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def _cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/model [provider/model] — switch the active LLM."""
    cfg: _config.Config = context.bot_data["cfg"]
    arg = " ".join(context.args or []).strip()
    if not arg:
        from argus.data.providers import PROVIDERS
        lines = [f"*Current:* `{cfg.agent.default_provider}/{cfg.agent.default_model}`\n",
                 "Switch with `/model <provider>/<model>`\n\n*Available providers:*"]
        for p in PROVIDERS:
            lines.append(f"• `{p.id}` — {p.label}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return
    if "/" not in arg:
        await update.message.reply_text("Format: `/model groq/llama-3.3-70b-versatile`",
                                          parse_mode=ParseMode.MARKDOWN)
        return
    prov, model = arg.split("/", 1)
    cfg.agent.default_provider = prov.strip()
    cfg.agent.default_model = model.strip()
    _config.save_config(cfg)
    await update.message.reply_text(
        f"✓ Model → `{cfg.agent.default_provider}/{cfg.agent.default_model}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_tools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tools — list active tools the agent can call right now."""
    from argus.tools.registry import register_defaults, tools_for
    cfg: _config.Config = context.bot_data["cfg"]
    register_defaults()
    tools = tools_for(cfg.agent.toolsets, disabled=cfg.agent.disabled_toolsets)
    if not tools:
        await update.message.reply_text("No tools active. Check `/status`.")
        return
    by_set: dict[str, list[str]] = {}
    for t in tools:
        by_set.setdefault(t.toolset, []).append(t.spec.name)
    lines = [f"*Active tools* — {len(tools)} across {len(by_set)} toolsets\n"]
    for ts, names in sorted(by_set.items()):
        lines.append(f"*{ts}*: " + ", ".join(f"`{n}`" for n in sorted(names)))
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode=ParseMode.MARKDOWN)


async def _cmd_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/commands — list every bot command + every skill."""
    cmds = "\n".join(f"`/{n}` — {d}" for n, d in _BOT_COMMANDS)
    skills = ""
    try:
        import json as _j
        from argus import paths
        idx = paths.HOME / "skills" / ".index.json"
        if idx.exists():
            data = _j.loads(idx.read_text())
            skills = "\n\n*Skills:*\n" + "\n".join(
                f"• `{name}`" for name in list(data.keys())[:20]
            )
    except Exception:
        pass
    await update.message.reply_text(
        f"*Commands:*\n{cmds}{skills}"[:4000],
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/debug — upload system info + recent log lines as a single message."""
    import platform as _plat, sys as _sys
    from argus import paths
    cfg: _config.Config = context.bot_data["cfg"]
    log_path = paths.LOGS_DIR / "argus-gateway.log"
    tail = ""
    if log_path.exists():
        try:
            tail = log_path.read_text()[-2000:]
        except Exception as e:
            tail = f"(log read failed: {e})"
    info = (
        f"*Debug bundle*\n\n"
        f"OS:           `{_plat.system()} {_plat.release()}`\n"
        f"Python:       `{_sys.version.split()[0]}`\n"
        f"Provider:     `{cfg.agent.default_provider}/{cfg.agent.default_model}`\n"
        f"Toolsets:     `{', '.join(cfg.agent.toolsets)}`\n"
        f"Voice:        `{cfg.voice.tts_provider}` mode=`{cfg.voice.tts_mode}`\n\n"
        f"*Log tail:*\n```\n{tail[-1500:]}\n```"
    )
    await update.message.reply_text(info[:4000], parse_mode=ParseMode.MARKDOWN)


async def _cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/restart — gracefully exit the polling loop so the supervisor (systemd /
    background script) brings us back up. If we're not supervised, this is a
    soft no-op."""
    await update.message.reply_text(
        "✓ Restart requested. If running under systemd, the unit will restart me.\n"
        "If running as a background process, you'll need to launch me again with\n"
        "`argus gateway start`.",
        parse_mode=ParseMode.MARKDOWN,
    )
    # Schedule a clean shutdown after the reply is delivered.
    import asyncio
    async def _shutdown():
        await asyncio.sleep(1.0)
        try:
            context.application.stop_running()
        except Exception:
            import os
            os._exit(0)
    asyncio.create_task(_shutdown())


async def _cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/update — surface the current version + how to update."""
    from argus import __version__
    await update.message.reply_text(
        f"*ARGUS* `v{__version__}`\n\n"
        "Update with:\n"
        "```\n"
        "cd <argus-repo>\n"
        "git pull && uv sync --extra voice\n"
        "argus gateway stop && argus gateway start\n"
        "```",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/approve <command> — single-use approval for the next run_command call."""
    cmd = " ".join(context.args or []).strip()
    if not cmd:
        await update.message.reply_text(
            "Format: `/approve <exact shell command>`\n\n"
            "Single-use. The next `run_command` call matching this string runs.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    from argus.tools.registry import approve_command
    approve_command(cmd)
    await update.message.reply_text(
        f"✓ Approved (one-shot):\n`{cmd}`", parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/deny — clear any pending approvals."""
    from argus.tools.registry import clear_approvals
    clear_approvals()
    await update.message.reply_text("✓ All pending approvals cleared.")


async def _cmd_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show how to connect external services (OAuth has to happen in CLI)."""
    await update.message.reply_text(
        "*Connect external services*\n\n"
        "OAuth requires a browser, so connectors are set up from the CLI on "
        "your computer. From a terminal:\n\n"
        "```\n"
        "argus connect           # picker for all services\n"
        "argus connect gmail     # just Gmail\n"
        "argus connect twitter\n"
        "argus connect linkedin\n"
        "argus connect calendar  # Google Calendar\n"
        "argus connect docs      # Google Docs / Drive\n"
        "argus connect outlook\n"
        "```\n\n"
        "Once connected, I can use them from here too — just ask me to "
        "send mail, read your inbox, schedule events, etc.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("telegram error: %s", context.error, exc_info=context.error)


# ── Bot-commands menu (the / autocomplete in the Telegram app) ──────────────


_BOT_COMMANDS = [
    ("start",        "Welcome panel — show what ARGUS is"),
    ("help",         "How ARGUS works"),
    ("status",       "Session info: provider, model, toolsets, voice"),
    ("report",       "Full status report — ARGUS + AgentBrain + Momento + Wire"),
    ("brain",        "Show memory dashboard"),
    ("sessions",     "Browse previous chat sessions"),
    ("model",        "Switch the LLM for this session"),
    ("tools",        "List active tools the agent can call"),
    ("speak",        "Convert your next message to a voice reply"),
    ("voice",        "Voice reply mode: off | voice_only | all"),
    ("connect",      "Connect Gmail, Twitter, LinkedIn, Calendar, …"),
    ("clear",        "Clear this conversation (keeps memory)"),
    ("clearmemory",  "Wipe MEMORY.md + USER.md (keeps keys & OAuth)"),
    ("commands",     "Show every command + skill (paginated)"),
    ("debug",        "Upload debug bundle (system info + logs)"),
    ("restart",      "Gracefully restart the gateway"),
    ("update",       "Check for ARGUS updates"),
    ("approve",      "Approve a pending shell command"),
    ("deny",         "Deny / cancel a pending command"),
]


async def _register_bot_menu(app: Application) -> None:
    """Push the slash-command menu to Telegram (BotFather setMyCommands).

    Critically: we DELETE any pre-existing menu first. This bot token may
    have been used by Hermes or another agent previously, and Telegram
    caches the command list per-token. Without delete_my_commands, users
    see ghost commands like /sessions, /resume, /debug from the old agent.
    """
    from telegram import BotCommand
    try:
        # 1. Wipe whatever was there (Hermes, default, custom — gone).
        try:
            await app.bot.delete_my_commands()
        except Exception:
            pass
        # 2. Push the ARGUS set.
        await app.bot.set_my_commands(
            [BotCommand(name, desc) for name, desc in _BOT_COMMANDS]
        )
        log.info("registered %d ARGUS bot commands (Hermes/legacy menu wiped)",
                 len(_BOT_COMMANDS))
        # 3. Also set the menu button label so the user sees "Argus" on the
        # menu button instead of the default "Menu".
        try:
            from telegram import MenuButtonCommands
            await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        log.warning("could not register bot commands: %s", e)


# ── Background pre-warmers ──────────────────────────────────────────────────


async def _prewarm_supertonic(app: Application) -> None:
    """Pre-load the Supertonic model so the first voice reply isn't a 30s wait.

    Runs once at gateway startup, in the background. Silent on any failure
    so the gateway still starts even without TTS.
    """
    cfg: _config.Config = app.bot_data["cfg"]
    if cfg.voice.tts_provider != "supertonic":
        return
    try:
        from argus.voice.tts import _supertonic_instance
        import asyncio
        await asyncio.to_thread(_supertonic_instance)
        log.info("supertonic model warmed up")
    except Exception as e:  # noqa: BLE001
        log.warning("supertonic pre-warm failed (TTS will still try on demand): %s", e)


# ── Application builder ───────────────────────────────────────────────────────


def build_application(cfg: _config.Config, token: str) -> Application:
    allowed_raw = _config.resolve_secret("TELEGRAM_ALLOWED_USERS", cfg) or ""
    allowed_ids = parse_allow_list(allowed_raw)

    if not allowed_ids:
        raise RuntimeError(
            "TELEGRAM_ALLOWED_USERS is empty — bot won't respond to anyone.\n\n"
            "Add yourself:\n"
            "  1. Message @userinfobot on Telegram to get your numeric ID\n"
            "  2. Run: argus telegram --allow <your-id>"
        )

    allow_filter = filters.User(user_id=list(allowed_ids))

    app = (
        ApplicationBuilder()
        .token(token)
        .rate_limiter(AIORateLimiter(
            overall_max_rate=30,
            overall_time_period=1,
            group_max_rate=20,
            group_time_period=60,
            max_retries=3,
        ))
        .build()
    )

    app.bot_data["cfg"]    = cfg
    app.bot_data["convos"] = {}

    # Public
    app.add_handler(CommandHandler("start", _cmd_start))

    # Restricted
    app.add_handler(CommandHandler("help",        _cmd_help,         filters=allow_filter))
    app.add_handler(CommandHandler("status",      _cmd_status,       filters=allow_filter))
    app.add_handler(CommandHandler("clear",       _cmd_clear,        filters=allow_filter))
    app.add_handler(CommandHandler("clearmemory", _cmd_clear_memory, filters=allow_filter))
    app.add_handler(CommandHandler("report",      _cmd_report,       filters=allow_filter))
    app.add_handler(CommandHandler("brain",       _cmd_brain,        filters=allow_filter))
    app.add_handler(CommandHandler("sessions",    _cmd_sessions,     filters=allow_filter))
    app.add_handler(CommandHandler("model",       _cmd_model,        filters=allow_filter))
    app.add_handler(CommandHandler("tools",       _cmd_tools,        filters=allow_filter))
    app.add_handler(CommandHandler("speak",       _cmd_speak,        filters=allow_filter))
    app.add_handler(CommandHandler("voice",       _cmd_voice,        filters=allow_filter))
    app.add_handler(CommandHandler("connect",     _cmd_connect,      filters=allow_filter))
    app.add_handler(CommandHandler("commands",    _cmd_commands,     filters=allow_filter))
    app.add_handler(CommandHandler("debug",       _cmd_debug,        filters=allow_filter))
    app.add_handler(CommandHandler("restart",     _cmd_restart,      filters=allow_filter))
    app.add_handler(CommandHandler("update",      _cmd_update,       filters=allow_filter))
    app.add_handler(CommandHandler("approve",     _cmd_approve,      filters=allow_filter))
    app.add_handler(CommandHandler("deny",        _cmd_deny,         filters=allow_filter))

    # Register the / autocomplete menu + pre-warm Supertonic at startup.
    async def _post_init(_app):
        await _register_bot_menu(_app)
        # Fire-and-forget: don't block startup on model download
        import asyncio as _aio
        _aio.create_task(_prewarm_supertonic(_app))
    app.post_init = _post_init

    # Media handlers — all behind allow_filter
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & allow_filter, _on_text))
    app.add_handler(MessageHandler(
        (filters.VOICE | filters.AUDIO) & allow_filter, _on_voice))
    app.add_handler(MessageHandler(
        filters.PHOTO & allow_filter, _on_photo))
    app.add_handler(MessageHandler(
        filters.Document.ALL & allow_filter, _on_document))
    app.add_handler(MessageHandler(
        filters.Sticker.ALL & allow_filter, _on_sticker))

    app.add_error_handler(_error_handler)
    return app


def run_long_polling(cfg: _config.Config) -> None:
    token = _config.resolve_secret("TELEGRAM_BOT_TOKEN", cfg)
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN not set.\n"
            "Run: argus setup   or   argus gateway setup"
        )
    log.info("starting Telegram gateway (long-polling)…")
    app = build_application(cfg, token)

    # Graceful shutdown on SIGTERM (systemd / supervisor will send this).
    # python-telegram-bot installs its own SIGINT/SIGTERM handlers via
    # stop_signals; we keep those defaults but log so journalctl shows the
    # drain. PTB drains updates + finishes in-flight handlers before exit.
    import signal as _signal
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        stop_signals=(_signal.SIGINT, _signal.SIGTERM, _signal.SIGABRT),
    )
    log.info("Telegram gateway shut down cleanly.")
