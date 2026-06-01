"""ARGUS CLI — Typer entry point that wires every subcommand."""

from __future__ import annotations

import typer

from argus import CODENAME, __version__
from argus.theme import console

app = typer.Typer(
    name="argus",
    help="ARGUS — the watchful agent that grows with you.",
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)


# Sub-app: gateway
gateway_app = typer.Typer(
    name="gateway",
    help="Manage the Telegram adapter (the only gateway).",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)
app.add_typer(gateway_app, name="gateway")

# Sub-app: memory
memory_app = typer.Typer(name="memory", help="View, edit, or wipe persistent memory.", no_args_is_help=True, add_completion=False, rich_markup_mode=None)
app.add_typer(memory_app, name="memory")

# Sub-app: skills
skills_app = typer.Typer(name="skills", help="List, install, enable, or remove SKILL.md packages.", no_args_is_help=True, add_completion=False, rich_markup_mode=None)
app.add_typer(skills_app, name="skills")

# Sub-app: sessions
sessions_app = typer.Typer(name="sessions", help="List, resume, export, or delete prior sessions.", no_args_is_help=True, add_completion=False, rich_markup_mode=None)
app.add_typer(sessions_app, name="sessions")

# Sub-app: config
config_app = typer.Typer(name="config", help="Read and write individual config.yaml keys.", no_args_is_help=True, add_completion=False, rich_markup_mode=None)
app.add_typer(config_app, name="config")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"argus {__version__} ({CODENAME})")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Print version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """If invoked with no subcommand, drop into interactive chat."""
    if ctx.invoked_subcommand is None:
        from argus.screens.chat import run as chat_run
        # Autostart is called inside chat.run() after the banner so the
        # status lines appear in the right visual position.
        chat_run()


# ─── chat ─────────────────────────────────────────────────────────────────────
@app.command("chat", help="Start an interactive chat session.")
def chat_cmd(
    query: str | None = typer.Option(None, "-q", "--query", help="One-shot mode: send a single prompt, print the reply, exit."),
    quiet: bool = typer.Option(False, "-z", help="Quiet one-shot — no banner, no spinner, just the reply on stdout."),
    provider: str | None = typer.Option(None, "--provider", help="Override the default provider for this session."),
    model: str | None = typer.Option(None, "--model", help="Override the default model for this session."),
    toolsets: str | None = typer.Option(None, "--toolsets", help="Restrict the agent to a named subset (a,b,c)."),
    cont: bool = typer.Option(False, "-c", "--continue", help="Resume the most recently active session."),
    resume: str | None = typer.Option(None, "-r", "--resume", help="Resume a specific session by ID or fuzzy title match."),
    ignore_user_config: bool = typer.Option(False, "--ignore-user-config", help="Ignore ~/.argus/config.yaml."),
) -> None:
    from argus.screens.chat import run as chat_run
    chat_run(
        one_shot=query,
        quiet=quiet,
        provider=provider,
        model=model,
        toolsets=toolsets.split(",") if toolsets else None,
        resume=resume or ("__last__" if cont else None),
    )


# ─── setup ────────────────────────────────────────────────────────────────────
@app.command("setup", help="Run the full onboarding wizard (provider + Telegram in one pass).")
def setup_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Walk the wizard but do not persist to ~/.argus/."),
    headless: bool = typer.Option(False, "--headless", help="Force headless/VPS mode for OAuth flows (paste callback URL manually)."),
) -> None:
    import os
    if headless:
        os.environ["ARGUS_HEADLESS"] = "1"
    from argus.screens.setup import run as setup_run
    setup_run(write=not dry_run)


# ─── key — fast path to inject one API key without re-running setup ───────────
@app.command("key", help="Set or update one provider's API key (skips the full setup wizard).")
def key_cmd(
    provider: str = typer.Argument(..., help="Provider id: groq | openai | anthropic | openrouter | gemini | deepseek | huggingface | ollama | custom"),
    api_key: str = typer.Argument(..., help="Your API key (will be validated against the provider then written 0600)."),
) -> None:
    from argus.screens.key import run as key_run
    key_run(provider, api_key)


# ─── doctor ───────────────────────────────────────────────────────────────────
@app.command("doctor", help="Run self-diagnostics: Python, API keys, Telegram, DB, disk, network.")
def doctor_cmd(
    all_green: bool = typer.Option(
        False,
        "--all-green",
        "-g",
        help="Force all checks to pass — useful for demos and screenshots.",
    ),
) -> None:
    from argus.screens.doctor import run as doctor_run
    code = doctor_run(force_all_green=all_green)
    raise typer.Exit(code=code)


# ─── model ────────────────────────────────────────────────────────────────────
@app.command("model", help="Open the provider/model selector — add, switch, or remove a provider.")
def model_cmd() -> None:
    from argus.screens.model import run as model_run
    model_run()


# ─── tools ────────────────────────────────────────────────────────────────────
@app.command("tools", help="Enable or disable individual toolsets (memory, web, files, shell, …).")
def tools_cmd() -> None:
    from argus.screens.tools import run as tools_run
    tools_run()


# ─── voice-debug — diagnose the entire voice stack in one command ────────────
@app.command("voice-debug", help="Diagnose the ARGUS voice stack: TTS, mic, wake word, STT, HUD window.")
def voice_debug_cmd() -> None:
    """Run each voice component and report exactly what works and what doesn't.
    Fix whatever fails, then run `argus talk`."""
    import asyncio, shutil, sys
    from rich.console import Console
    from rich.text import Text

    console = Console()
    console.print()
    console.print(Text("  ⟨◇⟩  ARGUS Voice Diagnostics", style="bold #FF38D1"))
    console.print(Text("  " + "─" * 50, style="#7A7A7A"))
    console.print()

    GOLD = "#FFC247"; CYAN = "#42E8F5"; DIM = "#7A7A7A"; RED = "#FF5C5C"

    def ok(label, detail=""):
        t = Text(); t.append(f"  ✓ {label:<28}", style=f"bold {GOLD}")
        if detail: t.append(detail, style=DIM)
        console.print(t)

    def fail(label, detail="", fix=""):
        t = Text(); t.append(f"  ✗ {label:<28}", style=f"bold {RED}")
        if detail: t.append(detail, style=DIM)
        console.print(t)
        if fix: console.print(Text(f"    Fix: {fix}", style=CYAN))

    def warn(label, detail=""):
        t = Text(); t.append(f"  ! {label:<28}", style=f"bold {CYAN}")
        if detail: t.append(detail, style=DIM)
        console.print(t)

    # ── 1. Audio player ──────────────────────────────────────────────────────
    player = next((p for p in ("afplay", "aplay", "paplay", "ffplay") if shutil.which(p)), None)
    if player:
        ok("Audio player", player)
    else:
        fail("Audio player", "none found", "brew install ffmpeg")

    # ── 2. edge-tts (network) ────────────────────────────────────────────────
    async def _test_edge():
        from argus.voice.listener import _synth_edge
        return await _synth_edge("Test.", timeout=6.0)

    path = asyncio.run(_test_edge())
    if path:
        ok("Edge TTS (network)", f"Ryan Neural — {path.stat().st_size:,} bytes")
        if player:
            import subprocess
            subprocess.run([player, str(path)], capture_output=True, timeout=5)
            ok("Edge TTS playback", "✓ played")
    else:
        warn("Edge TTS (network)", "timeout or unreachable — will use macOS say fallback")

    # ── 3. macOS say (offline) ───────────────────────────────────────────────
    if shutil.which("say"):
        async def _test_say():
            from argus.voice.listener import _synth_macos_say
            return await _synth_macos_say("Sentinel online.")
        path = asyncio.run(_test_say())
        if path:
            ok("macOS say (offline)", f"Daniel/Alex — {path.stat().st_size:,} bytes")
            if player:
                import subprocess
                subprocess.run([player, str(path)], capture_output=True, timeout=5)
                ok("macOS say playback", "✓ played — you should have heard it")
        else:
            fail("macOS say", "synthesis failed despite `say` being on PATH")
    else:
        fail("macOS say", "not on PATH (non-macOS?)")

    # ── 4. Microphone / sounddevice ──────────────────────────────────────────
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        ok("Microphone", dev["name"][:40])
    except ImportError:
        fail("sounddevice", "not installed",
              "uv sync --extra voice-wake  OR  pip install sounddevice")
    except Exception as e:
        fail("Microphone", str(e)[:60])

    # ── 5. faster-whisper (STT) ──────────────────────────────────────────────
    try:
        import faster_whisper  # noqa: F401
        ok("faster-whisper (STT)", "installed")
    except ImportError:
        fail("faster-whisper", "not installed",
              "uv sync --extra voice-wake  OR  pip install faster-whisper")

    # ── 6. Wake word / openwakeword ──────────────────────────────────────────
    try:
        from argus.voice.wakeword import Detector
        d = Detector("hey_argus")
        if d.is_available():
            ok("Wake word", d.describe())
        else:
            warn("Wake word", d.describe())
    except Exception as e:
        fail("Wake word", str(e)[:60], "uv sync --extra voice-wake")

    # ── 7. HUD binary resolution ─────────────────────────────────────────────
    from argus.voice.listener import _find_argus_binary
    bin_path = _find_argus_binary()
    ok("argus binary for HUD", bin_path)

    # ── 8. osascript (HUD window spawner) ───────────────────────────────────
    if shutil.which("osascript"):
        ok("osascript", "Terminal.app popups will work")
    else:
        warn("osascript", "no popup window — run `argus voice-hud` manually")

    console.print()
    console.print(Text("  ─" * 25, style=DIM))
    console.print(Text("  Run `argus talk` to start. Say 'Hey Argus' to wake.", style=DIM))
    console.print()


# ─── voice-hud — standalone voice HUD window ────────────────────────────────
@app.command("voice-hud", help="Open the ARGUS voice HUD (brand-colored waveform + live transcript). Closes voice mode when the window is shut.")
def voice_hud_cmd() -> None:
    """Run the voice HUD terminal. Called automatically when `argus talk` starts,
    or manually if you want to attach to an already-running voice session."""
    from argus.voice.hud import main as hud_main
    hud_main()


# ─── dashboard — real-time TUI cockpit ──────────────────────────────────────
@app.command("dashboard", help="Open the live ARGUS dashboard — status, active agents, tool events.")
def dashboard_cmd() -> None:
    """Full-screen Textual TUI. Press q to quit, r to refresh, c to clear events."""
    from argus.screens.dashboard import run_dashboard
    run_dashboard()


# ─── listen — voice-first hands-free mode ───────────────────────────────────
@app.command("talk", help="Start voice mode (opens HUD popup). Same as `argus listen`. Say 'End Argus' to close.")
def talk_cmd() -> None:
    """`argus talk` — friendly alias for the hands-free voice mode.

    Opens the HUD popup window, plays the greeting, and listens for
    'Hey Argus'. Say 'End Argus' or close the popup window to stop.
    """
    from argus.voice.listener import run_listener
    run_listener(
        wake=True,
        wake_model="hey_argus",
        stt_model="tiny.en",
        tts_provider=None,
        speak_replies=True,
    )


@app.command("listen", help="Hands-free voice mode: say 'Hey Argus', speak, hear the reply. 100% local OSS stack.")
def listen_cmd(
    no_wake: bool = typer.Option(
        False, "--no-wake",
        help="Skip wake-word detection — always listen. Useful when openwakeword isn't installed.",
    ),
    wake_model: str = typer.Option(
        "hey_argus", "--wake-model",
        help="Wake-word model. Looks first in ~/.argus/wake_models/<name>.onnx (custom-trained), then OWW pretrained (hey_jarvis, alexa, hey_mycroft, ok_nabu, weather, timer). Default 'hey_argus' auto-falls-back to 'hey_jarvis' until you train a custom one.",
    ),
    stt_model: str = typer.Option(
        "tiny.en", "--stt",
        help="Faster-Whisper model: tiny.en (39MB, fastest, English-only — DEFAULT) | tiny (multilingual) | base.en | base | small | medium | large-v3. Bigger = more accurate but slower; first call downloads the model (~40-1500 MB).",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q",
        help="Don't speak the reply (text-only). Useful when you want to read instead of listen.",
    ),
    tts_provider: str = typer.Option(
        "", "--tts",
        help="Override TTS provider for the spoken reply (supertonic|edge|openai|elevenlabs|piper). Empty = use config default.",
    ),
) -> None:
    """Start the voice listener loop.

    Flow:  Hey Argus  ->  Listening  ->  Thinking  ->  Speaking  ->  back to Asleep

    Requires the [voice-wake] extra:  uv sync --extra voice-wake
    """
    from argus.voice.listener import run_listener
    run_listener(
        wake=not no_wake,
        wake_model=wake_model,
        stt_model=stt_model,
        tts_provider=tts_provider or None,
        speak_replies=not quiet,
    )


# ─── voice ────────────────────────────────────────────────────────────────────
@app.command("voice", help="Test the TTS pipeline — synthesizes a sample phrase and plays it (macOS) or prints the path.")
def voice_cmd(
    text: str = typer.Option("Hello. ARGUS speaking — Sentinel online.", "--text", "-t",
                             help="Phrase to synthesize."),
    provider: str = typer.Option("", "--provider", "-p",
                                 help="Override config provider (supertonic|edge|openai|elevenlabs|piper)."),
    voice: str = typer.Option("", "--voice", "-v", help="Voice ID / name override."),
    play: bool = typer.Option(True, "--play/--no-play", help="Play the resulting clip (macOS: afplay)."),
) -> None:
    import asyncio
    import shutil
    import subprocess
    from rich.console import Console
    from argus import config as _config
    from argus.voice import synthesize, available

    console = Console()
    cfg = _config.load()
    if provider:
        cfg.voice.tts_provider = provider
    if voice:
        cfg.voice.tts_voice = voice

    ok, detail = available(cfg.voice.tts_provider)
    if not ok:
        console.print(f"[red]✗[/] provider [bold]{cfg.voice.tts_provider}[/] not ready: {detail}")
        raise typer.Exit(1)

    console.print(f"[cyan]⟨◇⟩[/] synthesizing with [bold]{cfg.voice.tts_provider}[/] / voice [bold]{cfg.voice.tts_voice}[/]…")
    # Prefer OGG (Telegram voice-bubble) when ffmpeg is available; else MP3
    # via pure-Python lameenc. Never leave the user with a raw WAV.
    preferred = "ogg" if shutil.which("ffmpeg") else "mp3"
    result = asyncio.run(synthesize(text, cfg=cfg, output_format=preferred))
    if not result:
        console.print("[red]✗[/] synthesis failed")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] wrote {result.path} ({result.format}, {result.duration_ms/1000:.1f}s)")

    if play and shutil.which("afplay"):
        subprocess.run(["afplay", str(result.path)])
    elif play and shutil.which("ffplay"):
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(result.path)])


# ─── gateway ──────────────────────────────────────────────────────────────────
@gateway_app.command("setup", help="Re-run the Telegram setup wizard (replace token, change allow-list).")
def gateway_setup_cmd() -> None:
    from argus.screens.gateway import setup_only
    setup_only()


@gateway_app.command("start", help="Start the gateway in the foreground (Ctrl-C to stop).")
def gateway_start_cmd(
    service: bool = typer.Option(False, "--service", help="Install + enable a systemd / launchd unit (dry-run preview here)."),
) -> None:
    from argus.screens.gateway import start
    start(as_service=service)


@gateway_app.command("stop", help="Stop a running gateway.")
def gateway_stop_cmd() -> None:
    from argus.screens.gateway import stop
    stop()


@gateway_app.command("restart", help="Reload config and re-validate the token without dropping messages.")
def gateway_restart_cmd() -> None:
    from argus.screens.gateway import restart
    restart()


@gateway_app.command("status", help="Print PID, uptime, allowed user count, last message time.")
def gateway_status_cmd() -> None:
    from argus.screens.gateway import status
    status()


@gateway_app.command("allow", help="Add a Telegram user ID to the allow-list (or --list to print).")
def gateway_allow_cmd(
    user_id: str | None = typer.Argument(None),
    list_: bool = typer.Option(False, "--list", help="Print the current allow-list."),
) -> None:
    from argus.screens.gateway import allow
    allow(user_id=user_id, list_only=list_)


@gateway_app.command("revoke", help="Remove a Telegram user ID from the allow-list.")
def gateway_revoke_cmd(user_id: str = typer.Argument(...)) -> None:
    from argus.screens.gateway import revoke
    revoke(user_id=user_id)


@gateway_app.command("logs", help="Tail the gateway log file.")
def gateway_logs_cmd(
    n: int = typer.Option(20, "-n", help="Lines to show."),
) -> None:
    from argus.screens.gateway import logs
    logs(n=n)


# ─── memory ───────────────────────────────────────────────────────────────────
@memory_app.command("show", help="Show USER.md and MEMORY.md side by side.")
def memory_show_cmd() -> None:
    from argus.screens.memory import show
    show()


@memory_app.command("add", help="Append a fact to MEMORY.md.")
def memory_add_cmd(fact: str = typer.Argument(...)) -> None:
    from argus.screens.memory import add
    add(fact)


@memory_app.command("clear", help="Wipe MEMORY.md (asks for confirmation).")
def memory_clear_cmd() -> None:
    from argus.screens.memory import clear
    clear()


# ─── skills ───────────────────────────────────────────────────────────────────
@skills_app.command("list", help="List installed skills.")
def skills_list_cmd() -> None:
    from argus.screens.skills import list_skills
    list_skills()


@skills_app.command("enable", help="Enable a skill by name.")
def skills_enable_cmd(name: str = typer.Argument(...)) -> None:
    from argus.screens.skills import set_enabled
    set_enabled(name, enabled=True)


@skills_app.command("disable", help="Disable a skill by name.")
def skills_disable_cmd(name: str = typer.Argument(...)) -> None:
    from argus.screens.skills import set_enabled
    set_enabled(name, enabled=False)


@skills_app.command("install", help="Install a skill from a path or URL (stubbed in this build).")
def skills_install_cmd(source: str = typer.Argument(...)) -> None:
    from argus.screens.skills import install
    install(source)


# ─── sessions ─────────────────────────────────────────────────────────────────
@sessions_app.command("list", help="List recent sessions.")
def sessions_list_cmd() -> None:
    from argus.screens.sessions import list_sessions
    list_sessions()


@sessions_app.command("tree", help="Show session lineage as a tree.")
def sessions_tree_cmd() -> None:
    from argus.screens.sessions import tree
    tree()


@sessions_app.command("resume", help="Resume a session by ID or fuzzy title.")
def sessions_resume_cmd(ref: str = typer.Argument(...)) -> None:
    from argus.screens.sessions import resume
    resume(ref)


@sessions_app.command("export", help="Export a session to JSONL.")
def sessions_export_cmd(session_id: str = typer.Argument(...)) -> None:
    from argus.screens.sessions import export
    export(session_id)


@sessions_app.command("delete", help="Delete a session by ID.")
def sessions_delete_cmd(session_id: str = typer.Argument(...)) -> None:
    from argus.screens.sessions import delete
    delete(session_id)


# ─── config ───────────────────────────────────────────────────────────────────
@config_app.command("list", help="Print every config key with secrets masked.")
def config_list_cmd() -> None:
    from argus.screens.config import list_config
    list_config()


@config_app.command("get", help="Read a single config key.")
def config_get_cmd(key: str = typer.Argument(...)) -> None:
    from argus.screens.config import get_config
    get_config(key)


@config_app.command("set", help="Write a single config key.")
def config_set_cmd(
    key: str = typer.Argument(...),
    value: str = typer.Argument(...),
) -> None:
    from argus.screens.config import set_config
    set_config(key, value)


# ─── update / rollback / uninstall ───────────────────────────────────────────
@app.command("update", help="Pull the latest release and re-install dependencies.")
def update_cmd(
    check: bool = typer.Option(False, "--check", help="Report what would change, do nothing."),
    channel: str = typer.Option("stable", "--channel", help="Release channel: stable | beta."),
) -> None:
    from argus.screens.update import run as update_run
    update_run(check=check, channel=channel)


@app.command("heal", help="Re-install all optional extras (voice, voice-wake, office, cron, vault, api). Run this if `argus doctor` shows missing deps.")
def heal_cmd() -> None:
    """Force-reinstall every optional extra. Fixes the case where a prior
    `uv sync` (without the right flags) silently dropped some dependencies."""
    import subprocess as _sp, os as _os
    from pathlib import Path as _Path
    from rich.console import Console as _Console

    console = _Console()
    project = _Path(__file__).resolve().parents[1]
    while not (project / "pyproject.toml").exists() and project.parent != project:
        project = project.parent
    if not (project / "pyproject.toml").exists():
        project = _Path.home() / "Desktop" / "Argus"

    console.print("[#42E8F5]⟨◇⟩[/#42E8F5] healing ARGUS — re-installing all extras…")
    venv = _Path.home() / "Library" / "Caches" / "argus" / "venv"
    env  = {**_os.environ, "UV_NO_EDITABLE": "1", "UV_PROJECT_ENVIRONMENT": str(venv)}
    rc = _sp.run(
        ["uv", "sync", "--extra", "voice", "--extra", "voice-wake",
         "--extra", "office", "--extra", "cron", "--extra", "vault",
         "--extra", "api", "--reinstall-package", "argus"],
        cwd=project, env=env,
    ).returncode
    if rc == 0:
        console.print("[#FFC247]✓ healed. Run `argus doctor` to verify.[/#FFC247]")
    else:
        console.print(f"[red]✗ uv sync exited {rc}.[/red] "
                       f"Try: cd {project} && uv sync --extra voice-wake")


@app.command("rollback", help="Revert to a backed-up version (most recent by default).")
def rollback_cmd(
    pick: bool = typer.Option(False, "--pick", help="Interactively choose which backup to restore."),
) -> None:
    from argus.screens.update import rollback
    rollback(pick=pick)


@app.command("uninstall", help="Remove ARGUS from this machine.")
def uninstall_cmd(
    purge: bool = typer.Option(False, "--purge", help="Non-interactive, removes ~/.argus entirely."),
) -> None:
    from argus.screens.uninstall import run as uninstall_run
    uninstall_run(purge=purge)


@app.command("reset", help="Move ~/.argus aside and start clean (keeps a backup).")
def reset_cmd(
    confirm: bool = typer.Option(False, "--confirm", "-y", help="Skip the prompt."),
) -> None:
    from argus.screens.reset import run as reset_run
    reset_run(confirm=confirm)


# ─── demo (bonus) ─────────────────────────────────────────────────────────────
@app.command("demo", help="Narrated tour of every screen — the fastest way to see the design.")
def demo_cmd(
    fast: bool = typer.Option(False, "--fast", help="Skip the pauses between screens."),
) -> None:
    from argus.screens.demo import run as demo_run
    demo_run(fast=fast)


# ─── telegram — fast path ─────────────────────────────────────────────────────
@app.command("telegram", help="Configure Telegram bot quickly: token, allow-list, and gateway.")
def telegram_cmd(
    allow: str | None = typer.Option(None, "--allow", "-a", help="User ID to add to the allow-list (persisted)."),
    revoke: str | None = typer.Option(None, "--revoke", "-r", help="User ID to remove from the allow-list."),
    list_: bool = typer.Option(False, "--list", "-l", help="Show the current allow-list."),
    start: bool = typer.Option(False, "--start", "-s", help="Start the gateway after configuring."),
) -> None:
    from argus.screens.gateway import allow as gw_allow, revoke as gw_revoke, start as gw_start, status as gw_status
    if allow:
        gw_allow(user_id=allow, list_only=False)
    elif revoke:
        gw_revoke(user_id=revoke)
    elif list_:
        gw_allow(user_id=None, list_only=True)
    else:
        from argus import config as _config
        cfg = _config.load()
        if _config.has_secret("TELEGRAM_BOT_TOKEN", cfg):
            gw_status()
        else:
            from argus.screens.gateway import setup_only
            setup_only()
    if start:
        gw_start()


# ─── me — interactive business onboarding (paste-a-URL flow) ─────────────────
@app.command("me", help="Teach ARGUS about your business — paste a website URL and it scans, learns, and remembers.")
def me_cmd(
    url: str | None = typer.Argument(
        None, help="Optional URL — skip the picker and go straight to scanning."
    ),
) -> None:
    """Opens the business-onboarding wizard. If a URL is passed, scrapes it
    directly; otherwise shows the existing profile (if any) and asks what to
    do. Either way, on success it resets the first-run flag so the next
    `argus` boot opens with a fresh intelligence briefing."""
    import asyncio
    from rich.console import Console
    from rich.text import Text
    from argus import business as _biz

    console = Console()

    # Fast path: URL passed directly → scrape, save, reset, summarise.
    if url:
        console.print(Text("  ⟨◇⟩  ", style="#FFC247")
                      .append(f"Scanning {url}…", style="#42E8F5"))
        ok, result = asyncio.run(_biz.onboard_from_url(url))
        if not ok:
            console.print(f"[red]✗[/] {result}")
            raise typer.Exit(1)
        _biz.reset_for_re_onboarding()
        console.print(f"[green]✓[/] Learned [bold]{result.get('name', url)}[/].")
        console.print("[dim]Next `argus` boot opens with a fresh briefing on this profile.[/]")
        return

    # Interactive path: shared wizard (same one used by chat REPL `argus me`).
    _biz.run_me_wizard(console)


# ─── business — re-learn / inspect / clear the company profile ───────────────
@app.command("business", help="Scrape your business website so ARGUS opens with a personalised welcome.")
def business_cmd(
    url: str | None = typer.Argument(None,
        help="Company URL to scrape. If omitted, shows the current profile."),
    clear: bool = typer.Option(False, "--clear", help="Forget the saved business profile."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-scrape the existing URL."),
) -> None:
    import asyncio
    from rich.text import Text
    from rich.panel import Panel
    from rich.console import Console
    from argus import business as _biz
    console = Console()

    if clear:
        if _biz.clear():
            console.print("[green]✓[/] business profile cleared.")
        else:
            console.print("[dim]nothing to clear.[/]")
        return

    if refresh:
        existing = _biz.load()
        if not existing or not existing.get("url"):
            console.print("[red]✗[/] no existing profile to refresh. Pass a URL.")
            raise typer.Exit(1)
        url = existing["url"]

    if url:
        console.print(f"[cyan]⟨◇⟩[/] scanning [bold]{url}[/]…")
        ok, result = asyncio.run(_biz.onboard_from_url(url))
        if not ok:
            console.print(f"[red]✗[/] {result}")
            raise typer.Exit(1)
        console.print(f"[green]✓[/] learned [bold]{result.get('name', url)}[/]")
        console.print(Panel(
            (f"  Tagline:  {result.get('tagline', '—')}\n"
             f"  Does:     {result.get('what_they_do', '—')}\n"
             f"  Products: {', '.join(result.get('products', [])[:6]) or '—'}\n"
             f"  Audience: {result.get('audience', '—')}"),
            border_style="#FF38D1",
        ))
        return

    # No args → show current
    profile = _biz.load()
    if not profile:
        console.print("[dim]No business profile yet.[/] Add one: [cyan]argus business https://yourcompany.com[/]")
        return
    console.print(Panel(
        (f"  [bold]{profile.get('name', '—')}[/]\n"
         f"  [dim]{profile.get('url', '')}[/]\n\n"
         f"  Tagline:  {profile.get('tagline', '—')}\n"
         f"  Does:     {profile.get('what_they_do', '—')}\n"
         f"  Products: {', '.join(profile.get('products', [])[:6]) or '—'}\n"
         f"  Audience: {profile.get('audience', '—')}"),
        border_style="#FF38D1", title="business profile", title_align="left",
    ))


# ─── connect — third-party service connectors ────────────────────────────────
@app.command("connect", help="Connect a third-party service: gmail, outlook, linkedin, twitter.")
def connect_cmd(
    service: str | None = typer.Argument(
        None,
        help="Service to connect: gmail | outlook | linkedin | twitter. "
             "Leave blank to see all available connectors.",
    ),
    status: bool = typer.Option(False, "--status", "-s", help="Show connection status for all connectors."),
    test:   bool = typer.Option(False, "--test",   "-t", help="Test an existing connection."),
    disconnect: bool = typer.Option(False, "--disconnect", "-d", help="Remove stored tokens."),
    headless: bool = typer.Option(False, "--headless", help="Force headless/VPS mode (paste callback URL manually instead of localhost server)."),
) -> None:
    import os
    if headless:
        os.environ["ARGUS_HEADLESS"] = "1"
    from argus.connectors.registry import list_connectors, run_connect_wizard, get_connector
    from argus.theme import console as new_console, GOLD, CYAN, DIM, MAGENTA
    from rich.table import Table
    from rich.text import Text
    console = new_console()

    if status:
        # `--status` → just list, don't prompt
        console.print()
        t = Table(show_header=True, header_style=f"bold {GOLD}", box=None, padding=(0, 2), pad_edge=False)
        t.add_column("Service",     style=CYAN, no_wrap=True)
        t.add_column("Status",      no_wrap=True)
        t.add_column("Account",     style=DIM)
        t.add_column("Connect via", style=DIM)
        for c in list_connectors():
            s = c.status()
            st = Text("✓ connected", style=GOLD) if s.connected else Text("○ not connected", style=DIM)
            t.add_row(f"{c.icon} {c.name}", st, s.account, f"argus connect {c.id}")
        console.print(t)
        console.print()
        return

    if service is None:
        # No service named → interactive picker with the 6 groupings the user asked for.
        from argus import picker
        from argus.banner import wordmark
        console.print()
        console.print(wordmark("CONNECT"))
        console.print()
        console.print(Text(
            "  Connect ARGUS to your external services. OAuth runs in your browser; "
            "tokens are stored 0600 in ~/.argus.",
            style=DIM,
        ))
        console.print()

        # Show inline status next to each choice so users see what's already done.
        by_id = {c.id: c for c in list_connectors()}
        def _badge(cid: str) -> str:
            c = by_id.get(cid)
            if not c:
                return ""
            try:
                return "  ✓ connected" if c.status().connected else ""
            except Exception:
                return ""

        groups = [
            ("__mail",           "✉️  Connect Mail",            "Gmail or Outlook (you'll pick on the next screen)"),
            ("twitter",          "𝕏  Connect Twitter / X",      "Read tweets, post, search" + _badge("twitter")),
            ("linkedin",         "💼 Connect LinkedIn",         "Post updates, read your profile" + _badge("linkedin")),
            ("google_calendar",  "📅 Connect Calendar",         "Google Calendar — events" + _badge("google_calendar")),
            ("__workspace",      "📂 Connect Google Workspace", "Gmail + Calendar + Docs in one OAuth"),
            ("__other",          "🔌 Others — custom API / MCP", "Paste docs URL → autonomous setup"),
        ]
        choice = picker.select(
            "what to connect",
            choices=[picker.Choice(value=g[0], label=g[1], description=g[2]) for g in groups],
            default=groups[0][0],
        )
        if not choice:
            return

        # Resolve choice → connector id(s) to run
        if choice == "__mail":
            sub = picker.select(
                "mail provider",
                choices=[
                    picker.Choice(value="gmail",   label="✉️  Gmail",   description=_badge("gmail")),
                    picker.Choice(value="outlook", label="✉️  Outlook", description=_badge("outlook")),
                ],
                default="gmail",
            )
            if not sub:
                return
            run_connect_wizard(sub, console)
            return
        if choice == "__workspace":
            # Google Workspace = Gmail + Calendar + Docs, sequentially
            console.print(Text("\n  Connecting Google Workspace (Gmail + Calendar + Docs)…\n", style=GOLD))
            for cid in ("gmail", "google_calendar", "google_docs"):
                run_connect_wizard(cid, console)
            return
        if choice == "__other":
            # Drop into the custom API/MCP sub-wizard from setup.py
            from argus.screens.setup import _other_api_wizard
            _other_api_wizard(console)
            return
        # Direct connector ID
        run_connect_wizard(choice, console)
        return

    c = get_connector(service.lower())
    if c is None:
        console.print(Text(f"\n  ✗ Unknown service '{service}'. Use: gmail, outlook, linkedin, twitter", style="argus.err_text"))
        return

    if disconnect:
        if c._token_file.exists():
            c._token_file.unlink()
            console.print(Text(f"  ✓ {c.name} tokens removed.", style=GOLD))
        else:
            console.print(Text(f"  {c.name} was not connected.", style=DIM))
        return

    if test:
        import asyncio
        ok, detail = asyncio.run(c.test_connection())
        icon = "✓" if ok else "✗"
        style = GOLD if ok else "argus.err_text"
        console.print(Text(f"\n  {icon} {c.name}: {detail}", style=style))
        return

    run_connect_wizard(service.lower(), console)


# ─── logs (top-level convenience) ─────────────────────────────────────────────
@app.command("logs", help="Tail the ARGUS log file.")
def logs_cmd(
    n: int = typer.Option(20, "-n", help="Lines to show."),
    grep: str | None = typer.Option(None, "--grep", help="Filter lines by a substring."),
) -> None:
    from argus.screens.gateway import logs
    logs(n=n, grep=grep)


# ─── vault ───────────────────────────────────────────────────────────────────

vault_app = typer.Typer(no_args_is_help=True, help="ARGUS Vault — local-first SQLite + vector memory.")
app.add_typer(vault_app, name="vault")


@vault_app.command("remember", help='Store a memory. Example: argus vault remember "Aniket prefers a deep British voice"')
def vault_remember_cmd(
    text:   str = typer.Argument(..., help="What to remember"),
    kind:   str = typer.Option("fact",  "--kind",   help="fact | snippet | event | user"),
    source: str = typer.Option("cli",   "--source", help="origin tag, e.g. cron:job1"),
    tags:   str = typer.Option("",      "--tags",   help="comma-separated tags"),
) -> None:
    from argus.vault import Vault
    from rich.console import Console
    eid = Vault().remember(text, kind=kind, source=source, tags=tags)
    Console().print(f"[#FFC247]✓ remembered #{eid}[/#FFC247] [#7A7A7A]({kind})[/#7A7A7A] {text[:80]}")


@vault_app.command("recall", help="Semantic search across memories.")
def vault_recall_cmd(
    query:     str   = typer.Argument(..., help="What to recall"),
    k:         int   = typer.Option(5,    "-k", "--top", help="How many results"),
    kind:      str   = typer.Option("",   "--kind", help="Filter by kind"),
    min_score: float = typer.Option(0.0,  "--min-score", help="Cosine similarity floor (0-1)"),
) -> None:
    from argus.vault  import Vault
    from rich.console import Console
    from rich.table   import Table
    results = Vault().recall(query, k=k, kind=kind or None, min_score=min_score)
    if not results:
        Console().print("[#7A7A7A]No matches.[/#7A7A7A]")
        return
    t = Table(title=f"Recall: {query!r}", header_style="bold #FF38D1", border_style="#7A7A7A")
    for col, justify in (("score", "right"), ("id", "right"), ("kind", "left"),
                          ("text", "left"), ("source", "left")):
        t.add_column(col, justify=justify)
    for e in results:
        t.add_row(f"{e.score:.3f}", str(e.id), e.kind, e.text[:80], e.source)
    Console().print(t)


@vault_app.command("list", help="List recent memories.")
def vault_list_cmd(
    kind:  str = typer.Option("",  "--kind",  help="Filter by kind"),
    limit: int = typer.Option(20,  "-n",      help="Max rows"),
) -> None:
    from argus.vault  import Vault
    from rich.console import Console
    from rich.table   import Table
    entries = Vault().all(kind=kind or None, limit=limit)
    if not entries:
        Console().print("[#7A7A7A]Vault is empty.[/#7A7A7A]")
        return
    t = Table(title="Vault entries", header_style="bold #FF38D1", border_style="#7A7A7A")
    for col in ("id", "kind", "created", "source", "text"):
        t.add_column(col)
    for e in entries:
        t.add_row(str(e.id), e.kind, e.created_at, e.source, e.text[:80])
    Console().print(t)


@vault_app.command("forget", help="Delete one memory by id.")
def vault_forget_cmd(entry_id: int) -> None:
    from argus.vault  import Vault
    from rich.console import Console
    ok = Vault().forget(entry_id)
    Console().print(f"[{'#FFC247' if ok else '#FF5C5C'}]"
                     f"{'✓ forgotten' if ok else '✗ no such id'} {entry_id}")


@vault_app.command("stats", help="Show vault size, backend, embedder.")
def vault_stats_cmd() -> None:
    from argus.vault  import Vault
    from rich.console import Console
    from rich.panel   import Panel
    s = Vault().stats()
    by_kind = ", ".join(f"{k}={v}" for k, v in s["by_kind"].items()) or "(none)"
    Console().print(Panel.fit(
        f"[bold #FFC247]⟨◇⟩ ARGUS Vault[/bold #FFC247]\n\n"
        f"  [#7A7A7A]path[/#7A7A7A]        [#F5E6C8]{s['path']}[/#F5E6C8]\n"
        f"  [#7A7A7A]entries[/#7A7A7A]     [#F5E6C8]{s['entries']}[/#F5E6C8]\n"
        f"  [#7A7A7A]by kind[/#7A7A7A]     {by_kind}\n"
        f"  [#7A7A7A]size[/#7A7A7A]        [#F5E6C8]{s['size_bytes']/1024:.1f} KB[/#F5E6C8]\n"
        f"  [#7A7A7A]backend[/#7A7A7A]     [#42E8F5]{s['vec_backend']}[/#42E8F5]\n"
        f"  [#7A7A7A]embedder[/#7A7A7A]    [#42E8F5]{s['embedder']}[/#42E8F5] ({s['dim']}-dim)",
        border_style="#FF38D1"))


# ─── cron ────────────────────────────────────────────────────────────────────

cron_app = typer.Typer(no_args_is_help=True, help="Schedule prompts to run on a cron expression — swarm-aware.")
app.add_typer(cron_app, name="cron")


@cron_app.command("add", help='Add a job. Schedule can be natural language: "every weekday at 9am"  OR  raw cron: "0 9 * * 1-5"')
def cron_add_cmd(
    schedule:  str  = typer.Argument(..., help='Schedule — NL or cron'),
    prompt:    str  = typer.Argument(..., help="Prompt for ARGUS to run on schedule"),
    name:      str  = typer.Option("",  "--name", "-n", help="Friendly label (default: first 40 chars of prompt)"),
    swarm:     str  = typer.Option("auto", "--swarm", help="Swarm mode: auto | always | never"),
    yes:       bool = typer.Option(False, "-y", "--yes", help="Skip confirmation"),
) -> None:
    from argus.cron.store  import CronStore
    from argus.cron.runner import _trigger_for
    from argus.cron.nl     import parse as nl_parse, local_tz_name
    from rich.console      import Console
    from rich.panel        import Panel
    from rich.prompt       import Prompt
    from datetime          import datetime

    console = Console()
    tz_name = local_tz_name()

    # ── Parse natural language OR raw cron ─────────────────────────────────
    expr, conf, msg = nl_parse(schedule)
    while expr is None:
        console.print(f"[#FF38D1]?[/#FF38D1] {msg}")
        more = Prompt.ask("[#42E8F5]>[/#42E8F5]", default="").strip()
        if not more:
            console.print("[#FF5C5C]Cancelled.[/#FF5C5C]")
            raise typer.Exit(1)
        expr, conf, msg = nl_parse(f"{schedule} {more}".strip())

    # ── Final validation via croniter (already done by nl.parse but re-check) ─
    try:
        _trigger_for(expr)
    except ValueError as e:
        console.print(f"[red]✗ invalid schedule:[/red] {e}")
        raise typer.Exit(1)

    # ── Preview the next 3 fire times in LOCAL timezone ────────────────────
    try:
        from croniter import croniter   # type: ignore
        it = croniter(expr, datetime.now().astimezone())
        next_three = [it.get_next(datetime).strftime("%a %Y-%m-%d %H:%M %Z") for _ in range(3)]
    except Exception:
        next_three = ["(could not preview)"]

    swarm_map = {"auto": -1, "always": 1, "never": 0}
    if swarm not in swarm_map:
        console.print(f"[red]✗ --swarm must be one of: auto, always, never[/red]")
        raise typer.Exit(1)

    # ── Confirm with the user before saving ───────────────────────────────
    panel = Panel.fit(
        f"[bold #FFC247]⟨◇⟩ Confirm new cron job[/bold #FFC247]\n\n"
        f"  [#7A7A7A]name[/#7A7A7A]      [#F5E6C8]{name or prompt[:40]}[/#F5E6C8]\n"
        f"  [#7A7A7A]prompt[/#7A7A7A]    [#F5E6C8]{prompt[:80]}{'…' if len(prompt) > 80 else ''}[/#F5E6C8]\n"
        f"  [#7A7A7A]schedule[/#7A7A7A]  [#42E8F5]{expr}[/#42E8F5]  [#7A7A7A]({msg or 'as entered'})[/#7A7A7A]\n"
        f"  [#7A7A7A]timezone[/#7A7A7A]  [#42E8F5]{tz_name}[/#42E8F5]\n"
        f"  [#7A7A7A]swarm[/#7A7A7A]     {swarm}\n\n"
        f"  [#FFC247]Next 3 fire times (local):[/#FFC247]\n"
        + "\n".join(f"    • {t}" for t in next_three),
        border_style="#FF38D1")
    console.print(panel)

    if not yes:
        ok = Prompt.ask("[#FFC247]Save this job?[/#FFC247]",
                         choices=["y", "n"], default="y")
        if ok.lower() != "y":
            console.print("[#7A7A7A]Cancelled.[/#7A7A7A]")
            raise typer.Exit(0)

    store = CronStore()
    job   = store.add(
        name      = name or prompt[:40],
        schedule  = expr,
        prompt    = prompt,
        use_swarm = swarm_map[swarm],
    )
    console.print(f"\n[#FFC247]✓ saved as id [bold]{job.id}[/bold][/#FFC247]")
    console.print(f"[#7A7A7A]Start the daemon to run jobs:[/#7A7A7A]")
    console.print(f"  [#42E8F5]argus cron daemon[/#42E8F5]   (foreground, Ctrl-C to stop)")
    console.print(f"  [#42E8F5]argus cron install-service[/#42E8F5]  (background, auto-starts)")


@cron_app.command("list", help="List all scheduled jobs.")
def cron_list_cmd() -> None:
    from argus.cron.store import CronStore
    from rich.console     import Console
    from rich.table       import Table

    console = Console()
    store   = CronStore()
    jobs    = store.list()

    if not jobs:
        console.print("[#7A7A7A]No jobs scheduled. Add one with `argus cron add`.[/#7A7A7A]")
        return

    table = Table(title="ARGUS Cron Jobs", header_style="bold #FF38D1",
                   border_style="#7A7A7A")
    table.add_column("id",       style="#F5E6C8")
    table.add_column("name",     style="#F5E6C8")
    table.add_column("schedule", style="#42E8F5")
    table.add_column("swarm")
    table.add_column("runs",     justify="right")
    table.add_column("enabled")

    swarm_label = {-1: "auto", 0: "never", 1: "always"}
    for j in jobs:
        table.add_row(
            j.id, j.name, j.schedule,
            swarm_label.get(j.use_swarm, "?"),
            str(j.run_count),
            "[#FFC247]✓[/#FFC247]" if j.enabled else "[#FF5C5C]✗[/#FF5C5C]",
        )
    console.print(table)


@cron_app.command("remove", help="Remove a scheduled job by id.")
def cron_remove_cmd(job_id: str) -> None:
    from argus.cron.store import CronStore
    from rich.console     import Console
    store   = CronStore()
    if store.delete(job_id):
        Console().print(f"[#FFC247]✓ removed job {job_id}[/#FFC247]")
    else:
        Console().print(f"[red]✗ no job with id {job_id}[/red]")
        raise typer.Exit(1)


@cron_app.command("enable",  help="Enable a job by id.")
def cron_enable_cmd(job_id: str)  -> None:
    from argus.cron.store import CronStore
    CronStore().set_enabled(job_id, True)
    from rich.console import Console; Console().print(f"[#FFC247]✓ enabled {job_id}[/#FFC247]")


@cron_app.command("disable", help="Disable a job by id (without deleting).")
def cron_disable_cmd(job_id: str) -> None:
    from argus.cron.store import CronStore
    CronStore().set_enabled(job_id, False)
    from rich.console import Console; Console().print(f"[#7A7A7A]paused {job_id}[/#7A7A7A]")


@cron_app.command("run", help="Fire a job ONCE right now (for testing).")
def cron_run_cmd(job_id: str) -> None:
    import asyncio
    from argus.cron.store  import CronStore
    from argus.cron.runner import run_job_once
    from rich.console      import Console
    from rich.panel        import Panel

    console = Console()
    store   = CronStore()
    job     = store.get(job_id)
    if not job:
        console.print(f"[red]✗ no job with id {job_id}[/red]")
        raise typer.Exit(1)

    console.print(f"[#42E8F5]⟨◇⟩ Running job '{job.name}'…[/#42E8F5]")
    run = asyncio.run(run_job_once(job, store=store))
    icon = "[#FFC247]✓[/#FFC247]" if run.status == "ok" else "[#FF5C5C]✗[/#FF5C5C]"
    console.print(Panel(
        f"{icon} status: [bold]{run.status}[/bold]   "
        f"swarm: {'yes' if run.used_swarm else 'no'}   "
        f"duration: {run.duration_ms}ms\n\n"
        f"{run.result or run.error or '(no output)'}",
        border_style="#FFC247" if run.status == "ok" else "#FF5C5C",
        title=f"Job run · {job.id}",
    ))


@cron_app.command("logs", help="Show recent job runs (optionally filtered by job id).")
def cron_logs_cmd(
    job_id: str = typer.Option("", "--id", help="Filter by job id"),
    limit:  int = typer.Option(15, "-n", help="Max rows to show"),
) -> None:
    from argus.cron.store import CronStore
    from rich.console     import Console
    from rich.table       import Table
    store   = CronStore()
    runs    = store.recent_runs(job_id=job_id or None, limit=limit)
    if not runs:
        Console().print("[#7A7A7A]No runs yet.[/#7A7A7A]")
        return
    table = Table(title="Recent runs", header_style="bold #FF38D1", border_style="#7A7A7A")
    for col in ("started", "job_id", "status", "swarm", "duration", "preview"):
        table.add_column(col)
    for r in runs:
        preview = (r.result or r.error or "")[:60].replace("\n", " ")
        table.add_row(
            r.started_at, r.job_id, r.status,
            "yes" if r.used_swarm else "no",
            f"{r.duration_ms}ms", preview,
        )
    Console().print(table)


@cron_app.command("daemon", help="Run the cron scheduler in the foreground.")
def cron_daemon_cmd() -> None:
    import asyncio
    from argus.cron.runner import run_daemon
    from rich.console      import Console
    Console().print("[#FFC247]⟨◇⟩ ARGUS cron daemon — Ctrl-C to stop[/#FFC247]")
    try: asyncio.run(run_daemon())
    except KeyboardInterrupt:
        Console().print("\n[#7A7A7A]daemon stopped.[/#7A7A7A]")


@cron_app.command("install-service", help="Install a launchd (macOS) or systemd (Linux) unit so the daemon auto-starts.")
def cron_install_service_cmd() -> None:
    import sys
    from argus.cron.service import write_launchd_plist, write_systemd_unit
    from rich.console       import Console
    console = Console()
    if sys.platform == "darwin":
        path = write_launchd_plist()
        console.print(f"[#FFC247]✓ wrote {path}[/#FFC247]\n\n"
                      f"Enable it with:\n  [#42E8F5]launchctl load -w {path}[/#42E8F5]\n"
                      f"\nDisable later with:\n  [#42E8F5]launchctl unload {path}[/#42E8F5]")
    else:
        path = write_systemd_unit()
        console.print(f"[#FFC247]✓ wrote {path}[/#FFC247]\n\n"
                      f"Enable it with:\n  [#42E8F5]systemctl --user daemon-reload && "
                      f"systemctl --user enable --now argus-cron.service[/#42E8F5]")


@app.command("serve", help="Run the ARGUS HTTP+WebSocket API server (for the mobile app or any HTTP client).")
def serve_cmd(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address (0.0.0.0 = all interfaces)."),
    port: int = typer.Option(8787, "--port", "-p", help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)."),
) -> None:
    """Start the API server. Mobile app connects to this.

    Requires:  uv sync --extra api
    """
    from argus.api.server import serve
    serve(host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
