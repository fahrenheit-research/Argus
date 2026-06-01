# Contributing to ARGUS

Thanks for considering a contribution. ARGUS is a self-hosted, local-first
multi-provider agent CLI — that constraint shapes what we accept.

## Ground rules

1. **Local-first.** New features should work without a network for the
   non-network parts, and without a paid SaaS dependency for the parts
   that need one (key/token/login = OK; mandatory hosted service = NO).
2. **Single-file backups.** Anything that stores user state lives under
   `~/.argus/` and stays trivially backup-able. No external services.
3. **Brand consistency.** Brand colors are fixed:
   - `#FF38D1` Electric Magenta — sigil brackets, CTAs, headings rule
   - `#FFC247` Acid Gold       — diamond, success, ARGUS responses
   - `#42E8F5` Ice Cyan        — info, links, user voice input
   - `#F5E6C8` Beige           — body text
4. **Tests required for new behavior.** `pytest -q` must stay green.
5. **No telemetry without explicit opt-in.** Period.

## Quick start

```bash
git clone <your-fork-url>
cd Argus
./bootstrap.sh              # sets up venv at ~/Library/Caches/argus/venv
# OR manually:
uv sync --extra voice --extra voice-wake --extra office --extra cron --extra vault --extra api
```

Run the test suite:

```bash
uv run pytest -q
```

Run the CLI:

```bash
./argus.sh                  # local launcher
# or
~/.local/bin/argus          # if shim installed
```

## Project layout

```
argus/                       Python source
├── api/                     FastAPI HTTP + WebSocket server  (argus serve)
├── cron/                    Persistent cron scheduler         (argus cron …)
├── vault/                   Local SQLite + vector memory      (argus vault …)
├── voice/                   Hands-free voice mode + HUD       (argus talk)
├── swarm/                   Constellation multi-agent system
├── providers/               Groq, OpenAI, Anthropic, Mistral, OpenRouter
├── tools/                   web, scribe (docx/pdf), ledger (xlsx), gmail, …
├── platforms/               telegram bridge
├── screens/                 chat REPL, setup wizard, doctor, etc.
└── ...
docs/                        User-facing documentation
tests/                       pytest suite
deploy/                      systemd units, install scripts
```

## Adding a new tool

1. Drop your handler in `argus/tools/<your_tool>.py` with a `register()`
   function that calls `argus.tools.registry.register(spec, handler)`.
2. Import the module from `argus.tools.registry.register_defaults()`.
3. Add tests in `tests/test_<your_tool>.py`.
4. Document the tool in `docs/tools.md`.

## Adding a new provider

1. Subclass `argus.providers.base.ProviderTransport` in
   `argus/providers/<name>.py`.
2. Register it in `argus/providers/registry.py:get_transport()`.
3. Add live-key validation in `argus/setup_keys.py`.
4. Add tests with a mocked HTTP layer.

## Adding a new toolset to default config

Update `argus/config.py:AgentConfig.toolsets`'s default list — the
config layer auto-merges saved configs with new defaults.

## Commit + PR convention

- Branch from `main`. Short topic branches: `feat/cron-daemon`,
  `fix/voice-echo-monitor`, `docs/vault-readme`.
- Conventional commits encouraged but not enforced:
  `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- Run `pytest -q` before pushing.
- One feature per PR; rebase rather than merge-commit.

## Code style

- Python 3.11+
- Type hints encouraged on public APIs; not required everywhere
- Follow existing patterns — most modules have a docstring header
  explaining design rationale; preserve that
- Prefer stdlib over a new dependency unless the dep is clearly load-bearing

## License

By contributing, you agree your contributions are licensed under
Apache-2.0 (see [LICENSE](LICENSE)).
