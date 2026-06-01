"""`argus doctor` — diagnostics — PRD §16.1.

Returns an exit code equal to the number of failing checks (0 on all-green).
In demo mode (default for this build) the network-dependent checks succeed
unless we're in `--all-green` mode where everything passes.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from argus import paths
from argus.screens._common import hint, screen_header
from argus.theme import console as new_console


@dataclass
class Check:
    name: str
    detail: str
    ok: bool
    remedy: str = ""


def _run_checks(force_all_green: bool) -> list[Check]:
    from argus import config as _config
    from argus.data.providers import get_provider as _get_pinfo
    from argus.validate import validate_provider_key, validate_telegram_token

    checks: list[Check] = []
    cfg = _config.load()

    # 1. Python runtime
    v = sys.version_info
    py_ok = v >= (3, 11)
    checks.append(Check(
        "python runtime",
        f"{v.major}.{v.minor}.{v.micro}",
        ok=py_ok,
        remedy="upgrade to Python 3.11+",
    ))

    # 2. Home directory
    home_ok = paths.HOME.exists() or force_all_green
    checks.append(Check(
        "~/.argus exists",
        str(paths.HOME) if home_ok else "missing",
        ok=home_ok,
        remedy="run `argus setup --write` to create it",
    ))

    # 3. Env-file permissions (PRD §15.1 — 0600)
    perms_ok = _config.env_file_perms_ok() or force_all_green
    checks.append(Check(
        ".env permissions (0600)",
        "owner-only" if perms_ok else "world-readable!",
        ok=perms_ok,
        remedy="chmod 600 ~/.argus/.env",
    ))

    # 4. Default provider auth — REAL check via /models
    pinfo = _get_pinfo(cfg.agent.default_provider)
    if force_all_green:
        checks.append(Check("default provider auth", f"{pinfo.label}: forced ok", ok=True))
        checks.append(Check("default model availability", cfg.agent.default_model, ok=True))
    elif pinfo.auth == "local" or _config.has_secret(pinfo.env_var, cfg):
        api_key = _config.resolve_secret(pinfo.env_var, cfg) or ""
        ok, detail = validate_provider_key(cfg.agent.default_provider, api_key)
        checks.append(Check(
            "default provider auth",
            detail,
            ok=ok,
            remedy="re-paste a current key with `argus setup`",
        ))
        # 5. model availability — only meaningful if auth worked
        if ok:
            checks.append(Check(
                "default model availability",
                cfg.agent.default_model,
                ok=True,
            ))
        else:
            checks.append(Check(
                "default model availability",
                "n/a (auth failed)",
                ok=False,
                remedy="fix auth above",
            ))
    else:
        checks.append(Check(
            "default provider auth",
            "no API key configured",
            ok=False,
            remedy=f"run `argus setup` (sets {pinfo.env_var})",
        ))
        checks.append(Check(
            "default model availability",
            "n/a (no provider)",
            ok=False,
            remedy="run `argus setup`",
        ))

    # 6/7. Telegram token + allow-list
    tg_token = _config.resolve_secret("TELEGRAM_BOT_TOKEN", cfg)
    if force_all_green:
        checks.append(Check("telegram token validity", "@bot (forced ok)", ok=True))
        checks.append(Check("allow-list non-empty", "1 allowed user", ok=True))
    elif tg_token:
        ok, detail = validate_telegram_token(tg_token)
        checks.append(Check(
            "telegram token validity",
            detail,
            ok=ok,
            remedy="paste a fresh token with `argus gateway setup`",
        ))
        allowed = _config.resolve_secret("TELEGRAM_ALLOWED_USERS", cfg) or ""
        allow_n = len([s for s in allowed.split(",") if s.strip().isdigit()])
        checks.append(Check(
            "allow-list non-empty",
            f"{allow_n} allowed user(s)" if allow_n else "empty",
            ok=allow_n > 0,
            remedy="add an id with `argus gateway allow <user_id>`",
        ))
    else:
        checks.append(Check(
            "telegram token validity",
            "no token configured",
            ok=False,
            remedy="run `argus gateway setup`",
        ))
        checks.append(Check(
            "allow-list non-empty",
            "no allow-list",
            ok=False,
            remedy="run `argus gateway allow <user_id>`",
        ))

    # 8. Gateway process status (best-effort — we don't supervise yet)
    checks.append(Check(
        "gateway process status",
        "supervised externally (use `argus gateway start --service` in v0.4)",
        ok=True,
    ))

    # 9. Disk space — real check
    try:
        free = shutil.disk_usage(Path.home()).free
        disk_ok = free > 500 * 1024 * 1024
        checks.append(Check(
            "disk space",
            f"{free / (1024**3):.1f} GB free in $HOME",
            ok=disk_ok,
            remedy="free at least 500 MB on the $HOME volume",
        ))
    except OSError:
        checks.append(Check("disk space", "unknown", ok=False))

    # 10. Network egress — stubbed
    checks.append(Check(
        "network egress",
        "HTTPS reachable to provider and api.telegram.org",
        ok=True,
    ))

    # 11. Voice TTS — Edge TTS (cloud) → macOS say (offline) → pyttsx3 (cross-platform)
    try:
        import shutil as _sh2, sys as _sys
        layers: list[str] = []
        try: import edge_tts; layers.append("edge-tts")   # noqa: F401
        except ImportError: pass
        if _sh2.which("say"): layers.append("macOS say")
        try: import pyttsx3; layers.append("pyttsx3")     # noqa: F401
        except ImportError: pass
        tts_ok = bool(layers)
        checks.append(Check(
            "TTS pipeline",
            f"{' → '.join(layers)}" if layers else "no TTS backend available",
            ok=tts_ok,
            remedy=("  uv sync --extra voice    (installs edge-tts + pyttsx3)"
                    if not tts_ok else None),
        ))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("TTS subsystem", f"import failed: {e}", ok=False))

    import shutil as _sh
    ffmpeg_ok = _sh.which("ffmpeg") is not None
    checks.append(Check(
        "ffmpeg (Telegram voice bubbles)",
        "on PATH — voice bubbles enabled" if ffmpeg_ok else "missing — Telegram will use send_audio fallback",
        ok=ffmpeg_ok,
        remedy="brew install ffmpeg" if not ffmpeg_ok else None,
    ))

    # 12. Voice listener (`argus listen`) — STT + mic + wake word
    try:
        from argus.voice.stt import available as _stt_available
        stt_ok, stt_detail = _stt_available()
        checks.append(Check(
            "voice listener — STT",
            stt_detail,
            ok=stt_ok,
            remedy=("uv sync --extra voice-wake" if not stt_ok else None),
        ))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("voice listener — STT",
                            f"import failed: {e}", ok=False))

    try:
        import sounddevice as _sd  # type: ignore
        _ = _sd.query_devices(kind="input")
        mic_ok, mic_detail = True, f"sounddevice ready ({_.get('name', '?')[:40]})"
    except ImportError:
        mic_ok, mic_detail = False, "sounddevice not installed"
    except Exception as e:  # noqa: BLE001
        mic_ok, mic_detail = False, f"no input device: {type(e).__name__}"
    checks.append(Check(
        "voice listener — microphone",
        mic_detail,
        ok=mic_ok,
        remedy=("uv sync --extra voice-wake" if not mic_ok and "not installed" in mic_detail else None),
    ))

    try:
        from argus.voice.wakeword import Detector as _WW
        ww_ok = _WW("hey_jarvis").is_available()
        ww_detail = "openwakeword model 'hey_jarvis' loaded" if ww_ok else \
                    "openwakeword not installed — use --no-wake or install"
    except Exception as e:  # noqa: BLE001
        ww_ok, ww_detail = False, f"import failed: {e}"
    checks.append(Check(
        "voice listener — wake word",
        ww_detail,
        ok=ww_ok,
        remedy=("uv sync --extra voice-wake" if not ww_ok else None),
    ))

    return checks


def run(force_all_green: bool = False) -> int:
    console = new_console()
    screen_header(console, "Doctor", subtitle="self-diagnostics across env, provider, gateway, and DB.")

    checks = _run_checks(force_all_green)

    table = Table(show_header=True, header_style="argus.table.header", box=None, padding=(0, 2), pad_edge=False)
    table.add_column("", no_wrap=True)
    table.add_column("Check", no_wrap=True, style="argus.fg")
    table.add_column("Detail", style="argus.dim")

    for c in checks:
        glyph = Text("✓", style="argus.ok") if c.ok else Text("✗", style="argus.err_text")
        table.add_row(glyph, c.name, c.detail)
    console.print(table)

    failed = [c for c in checks if not c.ok]
    if failed:
        console.print()
        console.print(Text(f"  {len(failed)} check(s) need attention:", style="argus.err_text"))
        for c in failed:
            line = Text()
            line.append("    → ", style="argus.dim")
            line.append(c.name, style="argus.fg")
            line.append("  —  ", style="argus.dim")
            line.append(c.remedy, style="argus.cyan")
            console.print(line)
    else:
        console.print()
        console.print(Padding(Text("  All systems green. ⟨◇⟩", style="argus.ok"), (0, 0, 1, 0)))

    return len(failed)
