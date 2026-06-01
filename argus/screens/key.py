"""`argus key <provider> <api_key>` — fast path to set one key.

Validates against the provider's /models endpoint, then writes to
~/.argus/.env (0600) and updates ~/.argus/config.yaml's default_provider
if not already set.
"""

from __future__ import annotations

from argus import config as _config
from argus.data.providers import BY_ID
from argus.screens._common import err_panel, hint, ok_panel, screen_header
from argus.state import mask_secret
from argus.theme import console as new_console
from argus.validate import validate_provider_key


# Search / research backend keys — these are NOT LLM providers but still
# need to land in ~/.argus/.env. Setting one auto-enables it in the
# web_search / web_research tool chain (see argus/tools/research.py).
_SEARCH_BACKENDS = {
    "tavily":      ("TAVILY_API_KEY",       "Tavily — best for LLM agents (1k/mo free)"),
    "exa":         ("EXA_API_KEY",          "Exa — neural search (1k/mo free)"),
    "perplexity":  ("PERPLEXITY_API_KEY",   "Perplexity — synthesised answers + citations"),
    "brave":       ("BRAVE_SEARCH_API_KEY", "Brave Search — privacy-first general web"),
    "serpapi":     ("SERPAPI_KEY",          "SerpAPI — Google results"),
}


def run(provider_id: str, api_key: str) -> None:
    console = new_console()
    screen_header(console, f"Set {provider_id} API key")
    pid = provider_id.lower().strip()
    api_key = api_key.strip()
    if not api_key:
        err_panel(console, "Empty key.")
        return

    # ── Search-backend path: no LLM validation, just persist ──────────
    if pid in _SEARCH_BACKENDS:
        env_var, label = _SEARCH_BACKENDS[pid]
        cfg = _config.load()
        env = dict(cfg.env)
        env[env_var] = api_key
        _config.save_env(env)
        ok_panel(console, f"✓ {label} key saved",
                 sub=f"Stored as {env_var}  →  web_search / web_research will "
                     f"now auto-route to {pid}.")
        return

    # ── LLM provider path: validate against /models then persist ──────
    if pid not in BY_ID:
        err_panel(console, f"Unknown provider '{provider_id}'",
                  sub=f"LLMs: {', '.join(sorted(BY_ID))}\n"
                      f"Search: {', '.join(sorted(_SEARCH_BACKENDS))}")
        return

    info = BY_ID[pid]
    hint(console, f"Validating with {info.label}…")
    ok, detail = validate_provider_key(pid, api_key)
    if not ok:
        err_panel(console, f"Key didn't work: {detail}")
        return
    console.print()
    ok_panel(console, f"✓ {detail}", sub=f"Storing as {info.env_var}")
    provider_id = pid   # for the code below that uses provider_id

    cfg = _config.load()
    env = dict(cfg.env)
    env[info.env_var] = api_key
    _config.save_env(env)

    # If no default provider was ever set, make this one the default.
    if cfg.agent.default_provider == "groq" and provider_id != "groq" and not _config.has_secret("GROQ_API_KEY", cfg):
        cfg.agent.default_provider = provider_id
        if info.models:
            cfg.agent.default_model = info.models[0].id
        _config.save_config(cfg)
        hint(console, f"Made {info.label} the default provider · model {cfg.agent.default_model}")

    console.print()
    hint(console, f"Saved to ~/.argus/.env  ({mask_secret(api_key)})")
    hint(console, "Run `argus` to chat with the real model now.")
