"""MCP / script / token paste interpreter.

When the user pastes a block of text that looks like configuration
(JSON, env vars, MCP server config, API tokens, shell scripts),
ARGUS detects it and offers to interpret + apply it — no manual
editing of config files required.

Supported paste types:
  mcp_config   — Claude Desktop / Cursor / generic MCP server JSON
  env_vars     — KEY=VALUE or export KEY=VALUE lines
  api_token    — bare token strings (gsk_, sk-, hf_, etc.)
  shell_script — #!/bin/bash or curl | bash style
  json_config  — generic JSON with recognisable ARGUS keys

Auto-generates clickable verification links where applicable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from argus import paths
from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA


# ── Pattern detection ─────────────────────────────────────────────────────────


_TOKEN_PREFIXES = (
    "gsk_",           # Groq
    "sk-",            # OpenAI / Anthropic
    "sk-ant-",        # Anthropic
    "hf_",            # HuggingFace
    "or-",            # OpenRouter
    "AIza",           # Google
    "xai-",           # xAI
    "ds-",            # DeepSeek
)

_MCP_KEYS = {"mcpServers", "mcp_servers", "servers", "command", "args", "env"}
_ARGUS_PROVIDER_KEYS = {"GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                         "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS",
                         "OPENROUTER_API_KEY", "GEMINI_API_KEY", "HF_TOKEN"}

_CLICKABLE_LINKS: dict[str, str] = {
    "GROQ_API_KEY":          "https://console.groq.com/keys",
    "OPENAI_API_KEY":        "https://platform.openai.com/api-keys",
    "ANTHROPIC_API_KEY":     "https://console.anthropic.com/settings/keys",
    "TELEGRAM_BOT_TOKEN":    "https://t.me/BotFather",
    "OPENROUTER_API_KEY":    "https://openrouter.ai/settings/keys",
    "GEMINI_API_KEY":        "https://makersuite.google.com/app/apikey",
    "HF_TOKEN":              "https://huggingface.co/settings/tokens",
    "DEEPSEEK_API_KEY":      "https://platform.deepseek.com/api_keys",
}


def classify_paste(text: str) -> str | None:
    """Return the category of pasted content, or None if not recognisable."""
    stripped = text.strip()

    # Bare API token
    for prefix in _TOKEN_PREFIXES:
        if stripped.startswith(prefix) and " " not in stripped and len(stripped) > 20:
            return "api_token"

    # JSON
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                if _MCP_KEYS & set(data.keys()):
                    return "mcp_config"
                if any(k in _ARGUS_PROVIDER_KEYS for k in data):
                    return "env_vars_json"
                return "json_config"
        except json.JSONDecodeError:
            pass

    # Env vars (KEY=VALUE or export KEY=VALUE)
    lines = stripped.splitlines()
    if lines and all(
        "=" in l.lstrip("export ") or l.strip().startswith("#")
        for l in lines if l.strip()
    ):
        keys = {l.split("=")[0].lstrip("export ").strip() for l in lines if "=" in l}
        if keys & _ARGUS_PROVIDER_KEYS:
            return "env_vars"

    # Shell script
    if stripped.startswith("#!/") or re.match(r"^(curl|wget|npm|npx|pip|uv)\s", stripped):
        return "shell_script"

    return None


# ── Handlers ──────────────────────────────────────────────────────────────────


def _link(url: str, label: str | None = None) -> Text:
    t = Text()
    t.append(label or url, style=f"bold {CYAN} link {url} underline")
    return t


def handle_api_token(token: str, console: Console) -> str | None:
    """Detect which provider the token belongs to and offer to set it up."""
    from argus.data.providers import PROVIDERS

    matched_provider = None
    for prefix in _TOKEN_PREFIXES:
        if token.startswith(prefix):
            for p in PROVIDERS:
                if prefix in p.env_var.lower() or prefix.rstrip("_-") in p.id:
                    matched_provider = p
                    break
            break

    if matched_provider is None:
        # Try by length/pattern heuristics
        if token.startswith("sk-ant-"):
            from argus.data.providers import BY_ID
            matched_provider = BY_ID.get("anthropic")
        elif token.startswith("sk-"):
            from argus.data.providers import BY_ID
            matched_provider = BY_ID.get("openai")

    if matched_provider:
        body = Text()
        body.append(f"\n  Detected: ", style=DIM)
        body.append(f"{matched_provider.label} API key\n", style=f"bold {GOLD}")
        body.append(f"  Key preview: {token[:8]}…{token[-4:]}\n", style=DIM)
        if matched_provider.env_var in _CLICKABLE_LINKS:
            body.append("  Verify at: ", style=DIM)
            body.append_text(_link(_CLICKABLE_LINKS[matched_provider.env_var],
                                   _CLICKABLE_LINKS[matched_provider.env_var]))
        console.print(body)
        return matched_provider.id
    return None


def handle_mcp_config(text: str, console: Console) -> dict[str, Any] | None:
    """Parse and preview an MCP config JSON."""
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        return None

    servers = data.get("mcpServers") or data.get("servers") or {}
    if not servers:
        return None

    body = Text()
    body.append(f"\n  📦 MCP config — {len(servers)} server(s):\n", style=f"bold {GOLD}")
    for name, cfg in list(servers.items())[:5]:
        cmd = cfg.get("command", "?")
        args = " ".join(str(a) for a in cfg.get("args", [])[:3])
        body.append(f"    • {name}: ", style=CYAN)
        body.append(f"{cmd} {args}\n", style=DIM)

    # Write to ~/.argus/mcp_config.json
    mcp_file = paths.HOME / "mcp_config.json"
    paths.ensure_dirs()
    existing: dict = {}
    if mcp_file.exists():
        try:
            existing = json.loads(mcp_file.read_text())
        except Exception:
            pass
    existing.setdefault("mcpServers", {}).update(servers)
    mcp_file.write_text(json.dumps(existing, indent=2))

    body.append(f"\n  ✓ Saved to {mcp_file}\n", style=CYAN)
    body.append("  MCP servers will be available on next ARGUS restart.", style=DIM)
    console.print(body)
    return data


def handle_env_vars(text: str, console: Console) -> dict[str, str]:
    """Parse KEY=VALUE lines and offer to persist recognised keys."""
    from argus import config as _cfg_mod
    from argus.state import mask_secret

    parsed: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip().lstrip("export").strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k:
                parsed[k] = v

    recognised = {k: v for k, v in parsed.items() if k in _ARGUS_PROVIDER_KEYS}
    unknown = {k: v for k, v in parsed.items() if k not in _ARGUS_PROVIDER_KEYS}

    if not recognised and not unknown:
        return {}

    body = Text()
    body.append(f"\n  🔑 Environment variables detected:\n", style=f"bold {GOLD}")

    for k, v in recognised.items():
        body.append(f"    ✓ {k}", style=CYAN)
        body.append(f" = {mask_secret(v)}", style=DIM)
        if k in _CLICKABLE_LINKS:
            body.append("  verify: ", style=DIM)
            body.append_text(_link(_CLICKABLE_LINKS[k], "↗"))
        body.append("\n")

    for k in unknown:
        body.append(f"    ? {k}", style=DIM)
        body.append(" (unknown to ARGUS — skipping)\n", style=DIM)

    console.print(body)
    return recognised


def handle_shell_script(text: str, console: Console) -> str:
    """Preview a shell script and copy it to workspace."""
    preview_lines = text.splitlines()[:8]
    body = Text()
    body.append("\n  📜 Shell script detected:\n", style=f"bold {GOLD}")
    for line in preview_lines:
        body.append(f"    {line}\n", style=DIM)
    if len(text.splitlines()) > 8:
        body.append(f"    … ({len(text.splitlines()) - 8} more lines)\n", style=DIM)

    # Save to workspace
    ws = Path.home() / "argus-workspace"
    ws.mkdir(exist_ok=True)
    script_file = ws / "pasted_script.sh"
    script_file.write_text(text)
    script_file.chmod(0o755)

    body.append(f"\n  ✓ Saved to ~/argus-workspace/pasted_script.sh\n", style=CYAN)
    body.append("  Run it with:  /approve ./pasted_script.sh  then ask ARGUS to execute.", style=DIM)
    console.print(body)
    return str(script_file)


# ── Auto-persist recognised env vars ─────────────────────────────────────────


def persist_env_vars(recognised: dict[str, str], console: Console) -> None:
    """Write recognised vars to ~/.argus/.env after user confirms."""
    from argus import config as _cfg_mod
    from argus.state import mask_secret

    cfg = _cfg_mod.load()
    env = dict(cfg.env)
    env.update(recognised)
    _cfg_mod.save_env(env)

    body = Text()
    body.append("\n  ✓ Written to ~/.argus/.env (0600)\n", style=CYAN)
    for k, v in recognised.items():
        body.append(f"    {k} = {mask_secret(v)}\n", style=DIM)
    body.append("\n  Run  argus doctor  to verify everything is connected.\n", style=DIM)
    console.print(body)
