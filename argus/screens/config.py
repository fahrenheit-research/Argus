"""`argus config` — read & write config, with masked secrets — PRD §14."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text

from argus.screens._common import err_panel, hint, ok_panel, screen_header
from argus.state import is_secret_key, mask_secret
from argus.theme import console as new_console

# A flat view of config.yaml + .env, with redaction.
_CONFIG_ROWS: list[tuple[str, str]] = [
    ("agent.default_provider", "groq"),
    ("agent.default_model", "llama-3.3-70b-versatile"),
    ("agent.toolsets", "[memory, web, files, shell]"),
    ("agent.disabled_toolsets", "[]"),
    ("agent.max_concurrent_tools", "8"),
    ("agent.fallback[0]", "openrouter / anthropic/claude-opus-4-7"),
    ("agent.fallback[1]", "ollama / llama3:8b"),
    ("auxiliary.compression", "groq / llama-3.1-8b-instant"),
    ("auxiliary.vision", "openai / gpt-4o-mini"),
    ("auxiliary.extract", "groq / llama-3.1-8b-instant"),
    ("providers.groq.base_url", "https://api.groq.com/openai/v1"),
    ("providers.groq.request_timeout_seconds", "60"),
    ("voice.stt_provider", "groq"),
    ("voice.stt_model", "whisper-large-v3"),
    ("gateway.telegram.enabled", "true"),
    ("gateway.telegram.reactions", "true"),
    ("gateway.telegram.forward_voice", "true"),
    ("gateway.telegram.edit_throttle_ms", "600"),
    ("gateway.telegram.max_message_length", "4000"),
    ("ui.color", "true"),
    ("ui.show_tool_previews", "true"),
    # secrets from .env — values masked
    ("GROQ_API_KEY", "gsk_DEMOdemoDEMOdemoDEMOdemoDEMOdemo3Xb2"),
    ("OPENAI_API_KEY", "sk-DEMOdemoDEMOdemoDEMOdemoDEMOdemodemoXY7p"),
    ("ANTHROPIC_API_KEY", "sk-ant-DEMOdemoDEMOdemoDEMOdemoDEMOA9bc"),
    ("TELEGRAM_BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrSTUvwxYZdemo01"),
    ("TELEGRAM_ALLOWED_USERS", "487293841,902113847"),
]


def list_config() -> None:
    console = new_console()
    screen_header(console, "Config", subtitle="merged view of ~/.argus/config.yaml and ~/.argus/.env (secrets masked).")

    t = Table(show_header=True, header_style="argus.table.header", box=None, padding=(0, 2), pad_edge=False)
    t.add_column("Key", style="argus.cyan", no_wrap=True)
    t.add_column("Value", style="argus.fg")
    for key, value in _CONFIG_ROWS:
        display = mask_secret(value) if is_secret_key(key) else value
        # Pass Text objects so bracketed values like "[memory, web, …]" aren't
        # eaten by Rich's console markup interpreter.
        t.add_row(Text(key, style="argus.cyan"), Text(display, style="argus.fg"))
    console.print(t)
    console.print()
    hint(console, "Read:  argus config get <key>")
    hint(console, "Write: argus config set <key> <value>")


def get_config(key: str) -> None:
    console = new_console()
    screen_header(console, "Config · get")
    matches = [(k, v) for k, v in _CONFIG_ROWS if k == key]
    if not matches:
        err_panel(console, f"no key '{key}' in config.")
        return
    k, v = matches[0]
    line = Text()
    line.append(f"  {k}", style="argus.cyan")
    line.append("  =  ", style="argus.dim")
    line.append(mask_secret(v) if is_secret_key(k) else v, style="argus.fg")
    console.print(line)


def set_config(key: str, value: str) -> None:
    console = new_console()
    screen_header(console, "Config · set")
    if is_secret_key(key):
        ok_panel(console, f"  {key}  =  {mask_secret(value)}", sub="Secret stored in ~/.argus/.env with perms 0600.")
    else:
        ok_panel(console, f"  {key}  =  {value}", sub="Written to ~/.argus/config.yaml.")
    hint(console, "(dry-run in this build — pass --write to persist)")
