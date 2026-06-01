"""Config layer — load/save ~/.argus/.env and ~/.argus/config.yaml.

This is the single source of truth for "what API keys does the user have,
what's their default provider/model, what's the allow-list, etc." Every
runtime component reads through `load()`; every persistence path writes
through `save_env()` / `save_config()` to keep secret-handling consistent
(0600 perms, no logging).

The schema is the full PRD §14.1 — no fields dropped. Sensible defaults
match the PRD's chosen first-class provider (Groq).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from argus import paths
from argus.state import is_secret_key

# ---------------------------------------------------------------------------
# Schema (matches PRD §14.1)
# ---------------------------------------------------------------------------


@dataclass
class FallbackEntry:
    provider: str
    model: str


@dataclass
class AuxiliaryEntry:
    provider: str
    model: str


@dataclass
class TelegramConfig:
    enabled: bool = False
    reactions: bool = True
    forward_voice: bool = True
    edit_throttle_ms: int = 600
    max_message_length: int = 4000


@dataclass
class GatewayConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


@dataclass
class VoiceConfig:
    # ── Speech-to-text ────────────────────────────────────────────────
    stt_provider: str = "groq"  # groq | local | openai
    stt_model: str = "whisper-large-v3"

    # ── Text-to-speech ────────────────────────────────────────────────
    # Providers (in fallback order): edge (free MS neural, default) →
    #            macOS say (offline) → pyttsx3 (cross-platform).
    # Optional overrides: openai | elevenlabs | piper | none.
    tts_provider: str = "edge"
    tts_model: str = ""            # provider-specific; "" → provider default
    tts_voice: str = "en-GB-RyanNeural"   # British male, deep — Jarvis-like
    tts_speed: float = 1.0
    tts_lang:  str = "en"
    # Telegram per-chat reply mode: off | voice_only | all
    # "voice_only" = TTS reply ONLY when the user sent a voice note.
    tts_mode:  str = "voice_only"


@dataclass
class VisionConfig:
    """Hermes-pattern vision routing (see image_routing.py).

    image_input_mode:
      - auto   : native if model supports vision, else run vision_analyze first
      - native : always embed image data in the chat request
      - text   : always run vision_analyze first, prepend description as text
    """
    image_input_mode: str = "auto"
    provider: str = "auto"          # auto | openrouter | nous | gemini | main
    model: str = ""                 # "" → provider default
    timeout: int = 30
    download_timeout: int = 30


@dataclass
class AgentConfig:
    default_provider: str = "groq"
    default_model: str = "llama-3.3-70b-versatile"
    toolsets: list[str] = field(default_factory=lambda: [
        "memory", "web", "files", "shell", "delegation",
        # Hermes ports
        "code", "todo", "clarify", "voice", "vision",
        # Connector actions
        "mail",
        # Office document creation (scribe_docx, scribe_pdf, ledger_xlsx, etc.)
        "documents",
    ])
    disabled_toolsets: list[str] = field(default_factory=list)
    max_concurrent_tools: int = 8
    fallback: list[FallbackEntry] = field(default_factory=list)


@dataclass
class AuxiliaryConfigs:
    compression: AuxiliaryEntry = field(
        default_factory=lambda: AuxiliaryEntry("groq", "llama-3.1-8b-instant")
    )
    vision: AuxiliaryEntry = field(
        default_factory=lambda: AuxiliaryEntry("openai", "gpt-4o-mini")
    )
    extract: AuxiliaryEntry = field(
        default_factory=lambda: AuxiliaryEntry("groq", "llama-3.1-8b-instant")
    )
    embedding: AuxiliaryEntry = field(
        # OpenAI's small embedding model is the cheapest, most-supported
        # default; we override when the user's primary provider has its own
        # embeddings endpoint (see embeddings.default_for()).
        default_factory=lambda: AuxiliaryEntry("openai", "text-embedding-3-small")
    )


@dataclass
class ProviderOverride:
    base_url: str = ""
    request_timeout_seconds: int = 60


@dataclass
class UIConfig:
    color: bool = True
    show_tool_previews: bool = True


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    auxiliary: AuxiliaryConfigs = field(default_factory=AuxiliaryConfigs)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    providers: dict[str, ProviderOverride] = field(default_factory=dict)

    # Loaded secrets — kept on the dataclass so consumers don't have to
    # re-parse .env. NEVER persisted via to_yaml(); only via save_env().
    env: dict[str, str] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load() -> Config:
    """Read ~/.argus/.env and ~/.argus/config.yaml. Missing files → defaults."""
    cfg = Config()
    cfg.env = _load_env()
    cfg = _merge_yaml(cfg, _load_yaml())
    return cfg


def _load_env() -> dict[str, str]:
    if not paths.ENV_FILE.exists():
        return {}
    return {k: v for k, v in dotenv_values(paths.ENV_FILE).items() if v is not None}


def _load_yaml() -> dict[str, Any]:
    if not paths.CONFIG_FILE.exists():
        return {}
    try:
        return yaml.safe_load(paths.CONFIG_FILE.read_text()) or {}
    except yaml.YAMLError:
        return {}


def _merge_yaml(cfg: Config, raw: dict[str, Any]) -> Config:
    """Overlay YAML values on top of dataclass defaults."""
    if not raw:
        return cfg

    a = raw.get("agent", {})
    cfg.agent.default_provider = a.get("default_provider", cfg.agent.default_provider)
    cfg.agent.default_model = a.get("default_model", cfg.agent.default_model)

    # ── Additive toolset migration ────────────────────────────────────
    # Users with config.yaml from an older release saved a narrower
    # toolset list. If we just override with `a.get("toolsets", default)`,
    # they NEVER get new tools (mail, code, voice, vision, ...). Instead,
    # take the *union* of saved + default, then subtract explicit
    # disabled_toolsets. This way:
    #   - Existing users automatically get new tools as we ship them.
    #   - Power users can still opt out via `disabled_toolsets`.
    saved_toolsets = a.get("toolsets")
    default_toolsets = list(cfg.agent.toolsets)   # already set by dataclass
    if saved_toolsets is None:
        cfg.agent.toolsets = default_toolsets
    else:
        union: list[str] = []
        for t in list(saved_toolsets) + default_toolsets:
            if t not in union:
                union.append(t)
        cfg.agent.toolsets = union
    cfg.agent.disabled_toolsets = a.get("disabled_toolsets", cfg.agent.disabled_toolsets)
    cfg.agent.max_concurrent_tools = a.get("max_concurrent_tools", cfg.agent.max_concurrent_tools)
    cfg.agent.fallback = [
        FallbackEntry(e.get("provider", ""), e.get("model", ""))
        for e in a.get("fallback", [])
    ]

    aux = raw.get("auxiliary", {})
    for key in ("compression", "vision", "extract", "embedding"):
        v = aux.get(key)
        if isinstance(v, dict) and "provider" in v and "model" in v:
            setattr(cfg.auxiliary, key, AuxiliaryEntry(v["provider"], v["model"]))

    voice = raw.get("voice", {})
    cfg.voice.stt_provider = voice.get("stt_provider", cfg.voice.stt_provider)
    cfg.voice.stt_model    = voice.get("stt_model",    cfg.voice.stt_model)
    cfg.voice.tts_provider = voice.get("tts_provider", cfg.voice.tts_provider)
    cfg.voice.tts_model    = voice.get("tts_model",    cfg.voice.tts_model)
    cfg.voice.tts_voice    = voice.get("tts_voice",    cfg.voice.tts_voice)
    cfg.voice.tts_speed    = voice.get("tts_speed",    cfg.voice.tts_speed)
    cfg.voice.tts_lang     = voice.get("tts_lang",     cfg.voice.tts_lang)
    cfg.voice.tts_mode     = voice.get("tts_mode",     cfg.voice.tts_mode)

    vision = raw.get("vision", {})
    cfg.vision.image_input_mode = vision.get("image_input_mode", cfg.vision.image_input_mode)
    cfg.vision.provider         = vision.get("provider",         cfg.vision.provider)
    cfg.vision.model            = vision.get("model",            cfg.vision.model)
    cfg.vision.timeout          = vision.get("timeout",          cfg.vision.timeout)
    cfg.vision.download_timeout = vision.get("download_timeout", cfg.vision.download_timeout)

    gw = raw.get("gateway", {}).get("telegram", {})
    if gw:
        cfg.gateway.telegram.enabled = gw.get("enabled", cfg.gateway.telegram.enabled)
        cfg.gateway.telegram.reactions = gw.get("reactions", cfg.gateway.telegram.reactions)
        cfg.gateway.telegram.forward_voice = gw.get("forward_voice", cfg.gateway.telegram.forward_voice)
        cfg.gateway.telegram.edit_throttle_ms = gw.get("edit_throttle_ms", cfg.gateway.telegram.edit_throttle_ms)
        cfg.gateway.telegram.max_message_length = gw.get("max_message_length", cfg.gateway.telegram.max_message_length)

    ui = raw.get("ui", {})
    cfg.ui.color = ui.get("color", cfg.ui.color)
    cfg.ui.show_tool_previews = ui.get("show_tool_previews", cfg.ui.show_tool_previews)

    for pid, pov in (raw.get("providers") or {}).items():
        if isinstance(pov, dict):
            cfg.providers[pid] = ProviderOverride(
                base_url=pov.get("base_url", ""),
                request_timeout_seconds=pov.get("request_timeout_seconds", 60),
            )

    return cfg


def save_env(env: dict[str, str]) -> Path:
    """Write ~/.argus/.env with 0600 perms. Secrets only."""
    paths.ensure_dirs()
    body = []
    for k, v in sorted(env.items()):
        if v is None or v == "":
            continue
        body.append(f"{k}={v}")
    paths.ENV_FILE.write_text("\n".join(body) + "\n")
    # Enforce 0600 even if umask was permissive.
    paths.ENV_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return paths.ENV_FILE


def save_config(cfg: Config) -> Path:
    """Write ~/.argus/config.yaml from the dataclass tree (no secrets)."""
    paths.ensure_dirs()
    blob: dict[str, Any] = {
        "agent": {
            "default_provider": cfg.agent.default_provider,
            "default_model": cfg.agent.default_model,
            "toolsets": cfg.agent.toolsets,
            "disabled_toolsets": cfg.agent.disabled_toolsets,
            "max_concurrent_tools": cfg.agent.max_concurrent_tools,
            "fallback": [
                {"provider": e.provider, "model": e.model} for e in cfg.agent.fallback
            ],
        },
        "auxiliary": {
            "compression": {"provider": cfg.auxiliary.compression.provider, "model": cfg.auxiliary.compression.model},
            "vision":      {"provider": cfg.auxiliary.vision.provider,      "model": cfg.auxiliary.vision.model},
            "extract":     {"provider": cfg.auxiliary.extract.provider,     "model": cfg.auxiliary.extract.model},
            "embedding":   {"provider": cfg.auxiliary.embedding.provider,   "model": cfg.auxiliary.embedding.model},
        },
        "voice": {
            "stt_provider": cfg.voice.stt_provider,
            "stt_model":    cfg.voice.stt_model,
            "tts_provider": cfg.voice.tts_provider,
            "tts_model":    cfg.voice.tts_model,
            "tts_voice":    cfg.voice.tts_voice,
            "tts_speed":    cfg.voice.tts_speed,
            "tts_lang":     cfg.voice.tts_lang,
            "tts_mode":     cfg.voice.tts_mode,
        },
        "vision": {
            "image_input_mode": cfg.vision.image_input_mode,
            "provider":         cfg.vision.provider,
            "model":            cfg.vision.model,
            "timeout":          cfg.vision.timeout,
            "download_timeout": cfg.vision.download_timeout,
        },
        "gateway": {
            "telegram": {
                "enabled": cfg.gateway.telegram.enabled,
                "reactions": cfg.gateway.telegram.reactions,
                "forward_voice": cfg.gateway.telegram.forward_voice,
                "edit_throttle_ms": cfg.gateway.telegram.edit_throttle_ms,
                "max_message_length": cfg.gateway.telegram.max_message_length,
            }
        },
        "ui": {
            "color": cfg.ui.color,
            "show_tool_previews": cfg.ui.show_tool_previews,
        },
        "providers": {
            pid: {"base_url": pov.base_url, "request_timeout_seconds": pov.request_timeout_seconds}
            for pid, pov in cfg.providers.items() if pov.base_url
        },
    }
    paths.CONFIG_FILE.write_text(yaml.safe_dump(blob, sort_keys=False, default_flow_style=False))
    return paths.CONFIG_FILE


# ---------------------------------------------------------------------------
# Override-precedence helper (PRD §14.3)
# ---------------------------------------------------------------------------


def resolve_secret(env_var: str, cfg: Config | None = None) -> str | None:
    """Return the secret value for `env_var`, honouring PRD §14.3 precedence:
    process env (current shell) → ~/.argus/.env → compiled defaults (None).
    Never accepts a value via CLI flags — those are config-set explicitly.
    """
    if env_var in os.environ:
        return os.environ[env_var]
    if cfg and env_var in cfg.env:
        return cfg.env[env_var]
    if not cfg:
        env = _load_env()
        return env.get(env_var)
    return None


def has_secret(env_var: str, cfg: Config | None = None) -> bool:
    val = resolve_secret(env_var, cfg)
    return bool(val and val.strip())


# ---------------------------------------------------------------------------
# Sanity check used by `argus doctor` etc.
# ---------------------------------------------------------------------------


def env_file_perms_ok() -> bool:
    """True if ~/.argus/.env exists and is 0600 (owner-only). Important enough
    that the doctor surfaces it as a separate check."""
    if not paths.ENV_FILE.exists():
        return True  # nothing to leak
    mode = paths.ENV_FILE.stat().st_mode
    return (mode & 0o777) == 0o600


def mask_env_for_display(env: dict[str, str]) -> dict[str, str]:
    """Mask any secret-shaped value for safe pretty-printing."""
    from argus.state import mask_secret
    out: dict[str, str] = {}
    for k, v in env.items():
        out[k] = mask_secret(v) if is_secret_key(k) else v
    return out
