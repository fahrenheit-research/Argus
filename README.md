# ⟨ ◇ ⟩ ARGUS

**The watchful agent that grows with you.**

A self-hosted, multi-provider AI agent that lives in your terminal, talks
back through your speakers, schedules its own work, remembers across
sessions, and reaches you through Telegram + a mobile app.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-FFC247.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-FF38D1)
![Tests](https://img.shields.io/badge/tests-35/35-FFC247)

## What's in the box

| Surface | Command | What it does |
| --- | --- | --- |
| Chat REPL         | `argus`               | Streaming chat with brand-styled UI       |
| Voice mode        | `argus talk`          | Hands-free, deep British male voice, HUD popup |
| Telegram bot      | `argus gateway start` | Same agent, in your messages              |
| HTTP API          | `argus serve`         | REST + WebSocket bridge (mobile app uses this) |
| Cron scheduler    | `argus cron`          | Persistent scheduled prompts, swarm-aware |
| Memory vault      | `argus vault`         | Local-first SQLite + vector recall        |
| Multi-agent swarm | auto-triggered        | 14 roles working in parallel on multi-task prompts |

## Quick start

```bash
./bootstrap.sh             # one-step setup (installs a global `argus` shim too)
argus setup                # paste your API key — validated live, persisted to ~/.argus/
argus                      # banner + chat REPL, streams from your real provider
argus gateway start        # real Telegram long-polling, if you configured a bot token
argus doctor               # real network probes against your configured providers
```

`./bootstrap.sh` installs a `~/.local/bin/argus` shim so you can call `argus`
from anywhere. `./argus.sh` still works from inside the project as a fallback.

### Scheduled jobs + persistent memory

```bash
# Schedule a weekday-morning briefing — auto-swarms multi-task prompts
argus cron add "0 9 * * 1-5" "Brief me on overnight market moves and email the summary"
argus cron daemon                              # foreground (Ctrl-C to stop)
argus cron install-service                     # background, auto-starts on login

# Remember things across sessions; recall by meaning, not keyword
argus vault remember "I prefer a deep British male voice"
argus vault recall "what voice do I want"      # semantic search
```

See **[docs/cron.md](docs/cron.md)** · **[docs/vault.md](docs/vault.md)** · **[docs/api.md](docs/api.md)**.

### Recommended terminal setup

ARGUS is designed for a 14-pt monospace font on a dark background. Terminals
don't expose font size to subprocesses, so set this once in your terminal:

| Terminal | How |
| --- | --- |
| iTerm2 | Settings → Profiles → Text → Font → 14pt JetBrains Mono / Menlo |
| Apple Terminal | Settings → Profiles → Text → Font → 14pt Menlo |
| VS Code | `"terminal.integrated.fontSize": 14` |
| Ghostty | `font-size = 14` in `~/.config/ghostty/config` |
| Alacritty | `font.size: 14` in `~/.config/alacritty/alacritty.toml` |

### Run from chat — every config flow is a slash command

Inside the chat REPL, everything you'd do from the CLI works as a `/command`:

| Slash command | What it does |
| --- | --- |
| `/setup` | Full onboarding wizard |
| `/key <provider> <api-key>` | Validate + persist one provider key (fast path) |
| `/model` | Switch provider / model |
| `/telegram` | Telegram bot setup (token + allow-list) |
| `/gateway` | Show Telegram gateway status |
| `/config` | Print every config key (secrets masked) |
| `/doctor` | Live diagnostics (real network probes) |
| `/tools` | Live tool inventory |
| `/skills` | Live skill inventory + AgentMomento BM25 stats |
| `/memory` / `/memory add <fact>` | View / append to MEMORY.md |
| `/sessions` / `/resume <id>` | Past sessions |
| `/status` | Provider, model, tokens, ttft, gateway, approvals |
| `/approve <command>` | One-shot approval for a shell command |
| `/new` / `/restart` | Fresh session / hard reset |
| `/reset -y` | Wipe ~/.argus (keeps backup) |
| `/help` | List everything |

When the agent gets a request like "connect my telegram" or "switch to claude",
it will tell you the exact slash command to type — it doesn't try to perform
config changes mid-stream (which would be silent and unauditable).

### macOS gotchas

The project sits on `~/Desktop`, which on most Macs is synced by iCloud Drive,
and the venv tooling stack collides with that in two distinct ways. The
bootstrap script sidesteps both — these notes are here so you can recognise
the symptoms when they bite a teammate.

1. **iCloud conflict-renames the venv.** When two processes touch `.venv/` at
   once, iCloud creates a clone directory like `.venv/lib/python3.13 2/`
   (note the trailing ` 2`). Packages end up split across the two trees, so
   `import argus` succeeds but `import typer` doesn't. **Fix:** put the venv
   under `~/Library/Caches/argus/venv` (out of iCloud's reach), which is
   what `bootstrap.sh` does by setting `UV_PROJECT_ENVIRONMENT`.

2. **uv + Python 3.13 disagree about hidden `.pth` files.** uv stamps every
   file it writes into `.venv/` with the macOS `UF_HIDDEN` flag (purely
   cosmetic — keeps Finder tidy). Python 3.13's `site.py` skips any `.pth`
   file with that flag, so hatchling's editable shim never registers the
   project on `sys.path`. **Fix:** `scripts/postsync.sh` writes a
   non-underscore `argus.pth` and `chflags nohidden`s every `.pth` in
   site-packages. It's idempotent — re-run it any time after a stray
   `uv sync`.

If you ever bypass the wrapper, set the venv path explicitly:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/Library/Caches/argus/venv"
uv run argus …
```

## Subcommands

| Command                | Purpose                                       |
| ---------------------- | --------------------------------------------- |
| `./argus.sh`           | Interactive chat                              |
| `./argus.sh setup`     | Onboarding wizard (provider + Telegram)       |
| `./argus.sh chat`      | Same as no-arg form, with flags               |
| `./argus.sh model`     | Switch provider / model                       |
| `./argus.sh tools`     | Toggle toolsets                               |
| `./argus.sh gateway`   | Telegram adapter lifecycle                    |
| `./argus.sh memory`    | Show / add / clear persistent memory          |
| `./argus.sh skills`    | List installed skills                         |
| `./argus.sh sessions`  | List / resume / tree of past sessions         |
| `./argus.sh config`    | Read / write config                           |
| `./argus.sh doctor`    | Diagnostics                                   |
| `./argus.sh update`    | Pull latest release                           |
| `./argus.sh uninstall` | Remove ARGUS                                  |
| `./argus.sh demo`      | Walk every screen in 90 seconds               |

## Status — what's live vs still stubbed

### Live (real API calls, real network)

- **`argus setup` validates API keys at paste time** against the provider's
  `/models` endpoint (or a 1-token ping for Anthropic). No more "looks ok,
  fails on first chat."
- **`argus setup --write` persists** to `~/.argus/.env` (0600) and
  `~/.argus/config.yaml`.
- **Chat REPL streams from the real provider** when a key is configured.
  Tool calls execute for real (memory read/write, web_fetch, file ops in
  `~/argus-workspace`, shell with per-call approval).
- **Telegram gateway runs real long-polling** when a bot token is set —
  `python-telegram-bot` v21 with `AIORateLimiter`, allow-list middleware,
  600ms edit-throttle for streamed replies, `👀 / ✓ / ✗` reactions.
- **Provider transports** for Groq, OpenAI, OpenRouter, DeepSeek, Hugging
  Face, Ollama, and any OpenAI-compatible custom endpoint (via one shared
  class); Anthropic via its native SDK with **prompt caching** enabled.
- **Self-healing** retry with `retry-after` honor, exponential backoff,
  and a **fallback chain** (PRD §11.3) — set in `config.yaml` under
  `agent.fallback`. Tenacity-backed, structured logs.
- **Self-learning** via a single action-dispatched `memory` tool
  (Hermes-Agent pattern). The system prompt tells the model to write to
  `MEMORY.md` for durable facts; bounded at 32 KB; `§`-delimited entries
  with short-unique-substring replace/remove.
- **`argus doctor`** does real network probes against your configured
  provider and your Telegram token. `--all-green` still forces success
  for screenshots/demos.

### Stubbed / deferred

- **SQLite persistence (sessions, messages, skills_index)** — chat history
  lives in memory for the duration of a session. The schema is in
  [`argus/paths.py`](argus/paths.py) as a comment, ready to wire.
- **Voice transcription** — Telegram voice messages get a polite
  "queued for v0.2" reply. The Groq Whisper transport is in place; the
  audio-chunk pipeline isn't.
- **Skill loader** (`SKILL.md` frontmatter parser). The banner advertises
  skills; loading them as runnable tool wrappers is a v0.3 task.
- **Curator** (Hermes-style idle-time skill consolidation).
- **Embeddings** — Hermes doesn't actually use embeddings in core; we
  follow the same path (LLM-decided skill selection, substring memory
  search). Auxiliary embedding model is set in config for future use.
- **`systemd` / `launchd` units** — `argus gateway start --service`
  prints a dry-run preview.
- **One-liner install script** — `./bootstrap.sh` is the canonical
  installer for now.

## Architecture (Hermes-Agent patterns, ported)

```
argus/
├── loop.py             # run_turn() — the read-decide-act cycle
├── config.py           # ~/.argus/.env + config.yaml schema & I/O
├── validate.py         # live key/token validation (used by setup + doctor)
├── retry.py            # tenacity-based self-healing
├── providers/
│   ├── base.py         # ProviderTransport ABC + unified Delta stream
│   ├── openai_compatible.py   # Groq, OpenAI, OpenRouter, DeepSeek, …
│   ├── anthropic.py    # native, with prompt-cache markers
│   └── registry.py     # factory: id + config → live transport
├── tools/
│   └── registry.py     # memory / web_fetch / files / shell (gated)
├── platforms/
│   └── telegram.py     # real long-polling Application
└── screens/            # every CLI screen, designed to PRD spec
```

The key idea: every component talks to abstractions, never to a specific
vendor SDK. Add a new provider by writing one class; the chat REPL,
Telegram gateway, and agent loop don't care.

### Cleanup hint

You may have a stale `~/.local/bin/argus` symlink from a prior project at
`~/Desktop/Agent Argus/`. It will intercept the `argus` command on `PATH`
and fail with `/Users/.../Agent Argus/.venv/bin/python: No such file`. Use
`uv run argus …` (which bypasses `PATH`), or `rm ~/.local/bin/argus` if
you're done with that older project.

## Brand

| Token             | Hex      | Use                              |
| ----------------- | -------- | -------------------------------- |
| Electric Magenta  | #FF38D1  | Glyph brackets, prompt arrow     |
| Acid Gold         | #FFC247  | The diamond, success state       |
| Ice Cyan          | #42E8F5  | Status line, info, links         |
| Background        | #050505  | Terminal background reference    |
| Body              | #E6E6E6  | Foreground text on dark          |
