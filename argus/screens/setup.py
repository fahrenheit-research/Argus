"""`argus setup` — onboarding wizard from PRD §8, restyled to match the
Hermes-Agent picker/multi-select look (see screenshots).

Default behaviour is `--dry-run`: shows every screen, validates input shape,
but writes nothing to disk. Pass `--write` to actually create ~/.argus/.env
and ~/.argus/config.yaml.
"""

from __future__ import annotations

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from argus import config as _config
from argus import picker
from argus.banner import wordmark
from argus.data.providers import PROVIDERS, get_provider
from argus.screens._common import err_panel, hint, kv_line, ok_panel, screen_header
from argus.state import mask_secret
from argus.theme import console as new_console
from argus.validate import validate_provider_key, validate_telegram_token


# Platforms surfaced in the multi-select. Telegram is the only live gateway in
# v1.0 (PRD §5.1 / §12); every other platform is listed so the UI matches the
# Hermes-style multi-select but flagged disabled with the v0.5+ note.
_PLATFORMS: list[tuple[str, str, str | None]] = [
    ("telegram",   "📟 Telegram",                None),
    ("slack",      "💼 Slack",                   "planned for v0.5+"),
    ("discord",    "💬 Discord",                 "planned for v0.5+"),
    ("whatsapp",   "🟢 WhatsApp",                "planned for v0.5+"),
    ("signal",     "🛰  Signal",                 "planned for v0.5+"),
    ("matrix",     "🔒 Matrix",                  "planned for v0.5+"),
    ("email",      "✉️  Email",                  "planned for v0.5+"),
    ("sms",        "📲 SMS (Twilio)",            "planned for v0.5+"),
]


def _intro(console: Console) -> None:
    console.print()
    console.print(wordmark("SETUP"))
    console.print()


def run(write: bool = False) -> None:
    console = new_console()
    _intro(console)

    if not write:
        hint(console, "Running in dry-run mode — nothing will be written to disk.")
        hint(console, "Pass --write to persist ~/.argus/.env and ~/.argus/config.yaml.")
        console.print()

    # ── Step 1: provider ────────────────────────────────────────────────
    provider_id = picker.select(
        "provider",
        choices=[
            picker.Choice(value=p.id, label=f"{p.label}", description=p.blurb)
            for p in PROVIDERS
        ],
        default="groq",
    )
    if not provider_id:
        return
    provider = get_provider(provider_id)
    console.print()

    # ── Step 2: auth + LIVE validation ──────────────────────────────────
    api_key: str | None = None
    if provider.auth == "api_key":
        console.print(kv_line("provider", provider.label, value_style="argus.cyan"))
        while True:
            console.print(Text(f"Paste your {provider.label} API key (input is hidden):", style="argus.fg"))
            api_key = picker.password("")
            if not api_key:
                return
            console.print()
            console.print(Text("  [ Validating with the provider… ]", style="argus.dim"))
            ok, detail = validate_provider_key(provider_id, api_key.strip())
            if ok:
                console.print(Text("  ✓ key valid: ", style="argus.ok").append(detail, style="argus.cyan"))
                console.print(Text("  ✓ stored as: ", style="argus.dim").append(mask_secret(api_key), style="argus.cyan"))
                break
            err_panel(console, f"That key didn't work: {detail}",
                      sub="Re-copy from the provider's console and paste again, or Ctrl-C to exit.")
    elif provider.auth == "local":
        ok_panel(console, f"Auto-detected {provider.label} at {provider.base_url}",
                 sub="No API key required for a local endpoint.")
    console.print()

    # ── Step 3: model ───────────────────────────────────────────────────
    model_id = picker.select(
        "model",
        choices=[
            picker.Choice(value=m.id, label=m.id, description=m.description)
            for m in provider.models
        ],
        default=provider.models[0].id if provider.models else None,
    )
    if not model_id:
        return
    hint(console, "This is your default. Change it any time with /model in chat.")
    console.print()

    # ── Step 3b: optional backup model (self-healing fallback chain) ────
    backup_entries: list[_config.FallbackEntry] = []
    backup_api_keys: dict[str, str] = {}        # env_var → key (extra providers)
    want_backup = picker.confirm(
        "Add a backup model? (ARGUS falls back to it automatically if the "
        "primary errors, rate-limits, or times out)",
        default=True,
    )
    if want_backup:
        # Loop so the user can chain multiple backups (Hermes-style). Empty
        # provider selection breaks the loop.
        while True:
            console.print()
            console.print(Text(
                f"  Picking backup #{len(backup_entries) + 1} "
                f"(current chain: {provider.label}/{model_id}"
                + (", " + ", ".join(f"{e.provider}/{e.model}" for e in backup_entries) if backup_entries else "")
                + ")",
                style="argus.dim",
            ))
            backup_pid = picker.select(
                "backup provider",
                choices=(
                    [picker.Choice(value="__done__", label="✓ done — no more backups",
                                   description="finish backup configuration")]
                    + [picker.Choice(value=p.id, label=p.label, description=p.blurb)
                       for p in PROVIDERS if p.id != provider_id]
                ),
                default="__done__",
            )
            if not backup_pid or backup_pid == "__done__":
                break

            backup_provider = get_provider(backup_pid)

            # If we don't already have this provider's key, ask for it now.
            from argus import config as _cfg_mod
            existing = _cfg_mod.resolve_secret(backup_provider.env_var)
            backup_key = existing
            if not backup_key and backup_provider.auth == "api_key":
                console.print()
                console.print(Text(
                    f"  Paste your {backup_provider.label} API key (input hidden):",
                    style="argus.fg",
                ))
                backup_key = picker.password("")
                if not backup_key:
                    console.print(Text("  ↻ skipped — no key entered", style="argus.dim"))
                    continue
                console.print(Text("  [ Validating with the provider… ]", style="argus.dim"))
                ok, detail = validate_provider_key(backup_pid, backup_key.strip())
                if not ok:
                    err_panel(console, f"That key didn't work: {detail}",
                              sub="Picking another backup; this one was skipped.")
                    continue
                console.print(Text("  ✓ key valid: ", style="argus.ok")
                              .append(detail, style="argus.cyan"))
                backup_api_keys[backup_provider.env_var] = backup_key.strip()

            # Now pick the backup model.
            backup_model = picker.select(
                f"backup model on {backup_provider.label}",
                choices=[
                    picker.Choice(value=m.id, label=m.id, description=m.description)
                    for m in backup_provider.models
                ],
                default=backup_provider.models[0].id if backup_provider.models else None,
            )
            if not backup_model:
                continue
            backup_entries.append(_config.FallbackEntry(
                provider=backup_pid, model=backup_model,
            ))
            console.print(Text(
                f"  ✓ backup #{len(backup_entries)}: {backup_provider.label}/{backup_model}",
                style="argus.ok",
            ))
            console.print()
            # After each backup, ask if they want one more (cap at 3 so the
            # chain stays sane).
            if len(backup_entries) >= 3:
                console.print(Text("  Chain at 3 backups — that's plenty.", style="argus.dim"))
                break
            more = picker.confirm("Add another backup?", default=False)
            if not more:
                break
    console.print()

    # ── Step 4: which platforms to configure? ───────────────────────────
    console.print(Text("Which platforms do you want to configure?", style="argus.fg"))
    console.print(Text("Telegram is the only live gateway in v1.0; others are planned.", style="argus.dim"))
    console.print()
    platform_choices = [
        picker.Choice(
            value=pid,
            label=label,
            description="configured" if pid == "telegram" else "not configured",
            disabled=note,
            checked=(pid == "telegram"),
        )
        for pid, label, note in _PLATFORMS
    ]
    selected_platforms = picker.multiselect("platforms to configure", platform_choices)
    if selected_platforms is None:
        return
    console.print()

    bot_token: str | None = None
    bot_username: str | None = None
    allowed_users: list[str] = []
    wants_telegram = "telegram" in selected_platforms

    if wants_telegram:
        # ── Step 5: token source ────────────────────────────────────────
        tg_choice = picker.select(
            "an option for the Telegram bot",
            choices=[
                picker.Choice(value="have",        label="I have a BotFather token"),
                picker.Choice(value="walkthrough", label="Walk me through creating one"),
                picker.Choice(value="cancel",      label="Cancel"),
            ],
            default="have",
        )
        if not tg_choice or tg_choice == "cancel":
            return
        console.print()

        if tg_choice == "walkthrough":
            _botfather_walkthrough(console)

        # ── Step 6: bot token + LIVE validation ────────────────────────
        while True:
            console.print(Text("Paste your Telegram bot token:", style="argus.fg"))
            bot_token = picker.password("")
            if not bot_token:
                return
            console.print()
            console.print(Text("  [ Validating with Telegram getMe… ]", style="argus.dim"))
            ok, detail = validate_telegram_token(bot_token.strip())
            if ok:
                bot_username = detail.split()[0].lstrip("@")
                ok_panel(console, f"Connected as {detail}")
                break
            err_panel(console, f"That token didn't work: {detail}")

        # ── Step 7: allow-list warning + entry ─────────────────────────
        _allowlist_warning(console)
        add_now = picker.confirm("Add allowed users now?", default=True)
        if add_now:
            console.print()
            console.print(Text("Your Telegram USER ID is a number (not your @username).", style="argus.dim"))
            console.print(Text("Open Telegram and message @userinfobot — it replies with your ID.", style="argus.dim"))
            console.print()
            while True:
                allowed_user = picker.text("Paste your numeric user ID (blank to stop):")
                if allowed_user is None or not allowed_user.strip():
                    break
                if allowed_user.strip().isdigit():
                    allowed_users.append(allowed_user.strip())
                    console.print(
                        Text(f"  ✓ added {allowed_user.strip()}", style="argus.ok")
                    )
                else:
                    err_panel(console, "That doesn't look like a numeric user ID — try again.")
            if allowed_users:
                ok_panel(
                    console,
                    f"allow-list now has {len(allowed_users)} user(s).",
                    sub="You can add more later with:  argus gateway allow <user_id>",
                )

    # ── Step 8: optional connector setup (Mail, Calendar, Twitter, …) ───
    _run_connector_wizard(console)

    # ── Step 9: optional business onboarding (scrape company site) ──────
    _run_business_wizard(console)

    # ── Done banner + persist + summary + restart prompt ────────────────
    console.print()
    console.print(Text("─" * 60, style="argus.rule"))
    console.print(Text("✓ Messaging platforms configured!", style="argus.ok"))
    console.print()

    if write:
        _persist(provider_id, model_id, api_key, bot_token, allowed_users,
                 selected_platforms,
                 backup_entries=backup_entries,
                 extra_api_keys=backup_api_keys)
        sub = ".env is 0600 (owner-only)."
        if backup_entries:
            sub += f"  Fallback chain: " + " → ".join(
                f"{e.provider}/{e.model}" for e in backup_entries
            )
        ok_panel(console, "configuration written to ~/.argus/", sub=sub)

    _summary(console, provider_id, model_id, api_key, bot_token, bot_username,
             allowed_users, selected_platforms, wrote=write)

    if wants_telegram and write:
        console.print()
        start = picker.confirm(
            f"Start the Telegram gateway in the background now?",
            default=True,
        )
        if start:
            pid, err = _start_gateway_background()
            if pid:
                ok_panel(
                    console,
                    f"Telegram gateway started in background (pid {pid})",
                    sub=f"Bot: @{bot_username}\n"
                        f"Listening for {max(1, len(allowed_users))} allowed user(s)\n\n"
                        f"Send any message to @{bot_username} to test.\n"
                        f"Logs: ~/.argus/logs/argus-gateway.log\n"
                        f"Stop:  argus gateway stop",
                )
            else:
                err_panel(console, f"Could not start gateway: {err}",
                          sub="Try manually:  argus gateway start")
    elif wants_telegram and not write:
        hint(console, f"Run  argus gateway start  to connect the bot once you save config with --write.")


def _run_connector_wizard(console: Console) -> None:
    """Optional connector setup — Mail, Calendar, Twitter, LinkedIn, …

    Surface a multi-select of every OAuth/API connector ARGUS supports.
    Each chosen connector runs its own wizard inline. Everything is
    skippable — empty selection just moves on.
    """
    from argus.connectors.registry import list_connectors

    console.print()
    console.print(Text("─" * 60, style="argus.rule"))
    console.print(Text("Step 5 of 5 — Connect external services (optional)", style="argus.heading"))
    console.print(Text("Skip any — connect later from chat with: 'connect gmail', 'link twitter', …", style="argus.dim"))
    console.print()

    connectors = list_connectors()
    # Show connection status for each so users see what's already linked.
    choices: list[picker.Choice] = []
    seen_ids: set[str] = set()

    # Curated order matches user's requested wizard steps.
    preferred_order = [
        "gmail", "outlook", "twitter", "linkedin",
        "google_calendar", "google_docs",
    ]
    ordered: list = []
    by_id = {c.id: c for c in connectors}
    for cid in preferred_order:
        if cid in by_id:
            ordered.append(by_id[cid])
            seen_ids.add(cid)
    for c in connectors:
        if c.id not in seen_ids:
            ordered.append(c)

    # Group labels for "Connect Mail" and "Connect Google Workspace".
    group_labels = {
        "gmail":           "✉️  Gmail (Mail)",
        "outlook":         "✉️  Outlook (Mail)",
        "twitter":         "𝕏  Twitter / X",
        "linkedin":        "💼 LinkedIn",
        "google_calendar": "📅 Google Calendar",
        "google_docs":     "📝 Google Docs / Drive (Google Workspace)",
    }

    for c in ordered:
        try:
            already = c.status().connected
        except Exception:
            already = False
        label = group_labels.get(c.id, f"{getattr(c, 'icon', '◇')} {c.name}")
        desc  = "already connected" if already else getattr(c, "description", "")
        choices.append(picker.Choice(
            value=c.id, label=label, description=desc, checked=False,
        ))

    # Append "Others" sentinel — opens the API-or-MCP sub-wizard.
    choices.append(picker.Choice(
        value="__other__",
        label="🔌 Others (custom API or MCP server)",
        description="Paste docs URL → setup_api_from_docs runs autonomously",
    ))

    selected = picker.multiselect(
        "services to connect now (space to toggle, enter to confirm, esc to skip all)",
        choices,
    )
    if not selected:
        hint(console, "No connectors selected — you can always connect later from chat.")
        return

    for cid in selected:
        if cid == "__other__":
            _other_api_wizard(console)
            continue
        connector = next((c for c in ordered if c.id == cid), None)
        if not connector:
            continue
        try:
            connector.setup_wizard(console)
        except Exception as e:  # noqa: BLE001
            err_panel(console, f"{connector.name} setup failed: {e}",
                      sub="You can retry from chat:  connect " + connector.name.lower())


def _run_business_wizard(console: Console) -> None:
    """Optional: scrape the user's business website so future sessions can
    open with a personalised welcome and 3 daily action suggestions.

    Skippable. Re-runnable via `argus connect` → "Others" → "Re-learn business".
    """
    from argus import business as _biz

    console.print()
    console.print(Text("─" * 60, style="argus.rule"))
    console.print(Text("Optional — teach ARGUS your business (15 seconds)", style="argus.heading"))
    console.print(Text(
        "Paste your company website. ARGUS will scrape it once, remember "
        "the gist, and open every CLI session with three concrete things "
        "it can do for you today. Skip with Enter.",
        style="argus.dim",
    ))
    console.print()

    existing = _biz.load()
    if existing:
        console.print(Text(
            f"  Already know about: {existing.get('name', existing.get('url', '?'))}",
            style="argus.cyan",
        ))
        keep = picker.confirm("Keep the existing profile?", default=True)
        if keep:
            hint(console, f"Kept. Edit later by re-running argus setup or `argus business`.")
            return

    url = picker.text("Business website URL (or blank to skip):")
    if not url or not url.strip():
        hint(console, "Skipped. You can do this later: `argus business <url>`.")
        return
    url = url.strip()

    console.print(Text("  ⟨◇⟩  Scanning the site… (≤30s)", style="argus.gold"))
    import asyncio as _aio
    try:
        ok, result = _aio.run(_biz.onboard_from_url(url))
    except Exception as e:  # noqa: BLE001
        err_panel(console, f"Couldn't scrape that URL: {type(e).__name__}: {e}",
                  sub="Skipping business onboarding. You can retry: `argus business <url>`.")
        return

    if not ok:
        err_panel(console, f"Scrape failed: {result}",
                  sub="Skipping. Retry with: `argus business <url>`.")
        return

    # Summary panel
    profile = result   # dict
    body = Text()
    body.append("\n  ✓ Learned ", style="argus.ok")
    body.append(profile.get("name", "the business"), style="argus.cyan")
    body.append("\n")
    if profile.get("tagline"):
        body.append(f"     — {profile['tagline']}\n", style="argus.dim")
    if profile.get("what_they_do"):
        body.append(f"\n  {profile['what_they_do'][:300]}\n", style="argus.fg")
    if profile.get("products"):
        body.append(f"\n  Products: ", style="argus.dim")
        body.append(", ".join(profile["products"][:6]), style="argus.fg")
        body.append("\n")
    body.append(f"\n  Saved to ~/.argus/business.json — used at every CLI boot.",
                style="argus.dim")
    console.print(Padding(Panel(body, border_style="argus.magenta", padding=(0, 2)), (1, 0)))


def _other_api_wizard(console: Console) -> None:
    """Custom API / MCP sub-wizard — same flow the agent uses autonomously."""
    console.print()
    console.print(Text("🔌 Custom API or MCP server", style="argus.heading"))
    console.print(Text("Paste a documentation URL — ARGUS will fetch, detect auth, and persist the key.",
                       style="argus.dim"))
    console.print()
    name = picker.text("Friendly name for this API (e.g. 'Stripe'):") or ""
    docs_url = picker.text("Docs URL (or leave blank for MCP-only):") or ""
    api_key = picker.password("API key (optional — paste now or skip):") or ""

    if not docs_url and not api_key:
        hint(console, "Nothing to set up. Skipping.")
        return

    # Call the same tool the LLM agent uses, so the UX is identical.
    import asyncio
    from argus.tools.api_setup import _setup_api
    result = asyncio.run(_setup_api({
        "name": name.strip(),
        "docs_url": docs_url.strip(),
        "api_key": api_key.strip(),
    }))
    console.print(Padding(Panel(
        Text(result, style="argus.fg"),
        border_style="argus.magenta",
        padding=(1, 2),
    ), (1, 0)))


def _start_gateway_background() -> tuple[int | None, str]:
    """Launch the Telegram gateway as a detached background process.

    Uses the same Python interpreter that's currently running so the venv
    is always correct — no PATH lookups, no env-var magic needed.
    Returns (pid, error_message). pid is None on failure.
    """
    import os
    import subprocess
    import sys
    from argus import paths

    paths.ensure_dirs()
    log_file = paths.LOGS_DIR / "argus-gateway.log"

    try:
        # Run `python -c "from argus..."` so it uses the exact same Python.
        cmd = [
            sys.executable, "-c",
            (
                "from argus.config import load as _load; "
                "from argus.platforms.telegram import run_long_polling as _run; "
                "_run(_load())"
            ),
        ]
        with open(log_file, "a") as lf:
            proc = subprocess.Popen(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # detach: survives terminal close
                env={**os.environ},
            )

        # Write PID so `argus gateway stop` can find it
        paths.ensure_dirs()
        paths.TELEGRAM_PID.write_text(str(proc.pid))
        return proc.pid, ""

    except Exception as e:  # noqa: BLE001
        return None, str(e)


def _botfather_walkthrough(console: Console) -> None:
    body = Text()
    body.append("Creating a Telegram bot — 60 seconds\n", style="argus.heading")
    body.append("─────────────────────────────────────\n\n", style="argus.rule")
    steps = [
        ("1. ", "Open Telegram and search for ", "@BotFather"),
        ("2. ", "Send ", "/newbot"),
        ("3. ", "Pick a display name  (e.g. ", "\"My ARGUS Agent\")"),
        ("4. ", "Pick a username  (must end in 'bot', e.g. ", "my_argus_bot)"),
        ("5. ", "BotFather replies with a token that looks like:", ""),
    ]
    for num, txt, hl in steps:
        body.append(num, style="argus.cyan")
        body.append(txt, style="argus.fg")
        body.append(hl, style="argus.cyan")
        body.append("\n")
    body.append("       123456789:ABCdefGHIjklMNOpqrSTUvwxYZ\n", style="argus.gold")
    body.append("6. ", style="argus.cyan")
    body.append("Copy it. Come back here and paste it below.\n\n", style="argus.fg")
    body.append("⚠  ", style="argus.err_text")
    body.append("Keep this token secret. Anyone with it can control your bot.\n", style="argus.fg")
    body.append("   If it ever leaks, run ", style="argus.dim")
    body.append("/revoke", style="argus.cyan")
    body.append(" in BotFather to issue a new one.", style="argus.dim")

    console.print(Padding(Panel(body, border_style="argus.magenta", padding=(1, 2)), (1, 0)))


def _persist(
    provider_id: str,
    model_id: str,
    api_key: str | None,
    bot_token: str | None,
    allowed_users: list[str],
    selected_platforms: list[str],
    *,
    backup_entries: list[_config.FallbackEntry] | None = None,
    extra_api_keys: dict[str, str] | None = None,
) -> None:
    """Write ~/.argus/.env + ~/.argus/config.yaml from the wizard answers.
    Merges into any existing config (preserves keys not touched here)."""
    cfg = _config.load()

    # Apply wizard answers
    cfg.agent.default_provider = provider_id
    cfg.agent.default_model = model_id
    cfg.gateway.telegram.enabled = bool(bot_token and "telegram" in selected_platforms)

    # Backup chain — replace any existing chain. The self-heal loop in
    # argus/loop.py reads this list and walks it on primary failure.
    if backup_entries is not None:
        cfg.agent.fallback = list(backup_entries)

    # Auxiliary embedding default — point at the user's provider where
    # possible, fall back to OpenAI for providers that don't ship embeddings.
    from argus.providers.registry import _SPECS
    if provider_id in _SPECS and _SPECS[provider_id].embedding_model:
        cfg.auxiliary.embedding = _config.AuxiliaryEntry(
            provider=provider_id,
            model=_SPECS[provider_id].embedding_model,
        )

    _config.save_config(cfg)

    # Secrets — merge into existing .env (don't clobber other keys)
    env = dict(cfg.env)
    if api_key:
        info = get_provider(provider_id)
        env[info.env_var] = api_key.strip()
    if bot_token:
        env["TELEGRAM_BOT_TOKEN"] = bot_token.strip()
    if allowed_users:
        env["TELEGRAM_ALLOWED_USERS"] = ",".join(allowed_users)
    # Backup-provider API keys (one .env entry per distinct provider).
    if extra_api_keys:
        for env_var, key in extra_api_keys.items():
            if key:
                env[env_var] = key
    _config.save_env(env)


def _allowlist_warning(console: Console) -> None:
    body = Text()
    body.append("⚠  ", style="argus.err_text")
    body.append("Telegram has no user allow-list — anyone can use your bot!", style="argus.fg")
    console.print(Padding(body, (1, 0)))


def _summary(
    console: Console,
    provider_id: str,
    model_id: str,
    api_key: str | None,
    bot_token: str | None,
    bot_username: str | None,
    allowed_users: list[str],
    selected_platforms: list[str],
    *,
    wrote: bool,
) -> None:
    screen_header(
        console,
        "Summary",
        subtitle="What the wizard wrote to disk." if wrote else "What the wizard would write (use --write to persist).",
    )

    table = Table(show_header=False, show_edge=False, box=None, pad_edge=False, padding=(0, 2))
    table.add_column(style="argus.dim", justify="right", no_wrap=True)
    table.add_column(style="argus.cyan")

    table.add_row("file", "~/.argus/.env  (perms 0600)")
    provider = get_provider(provider_id)
    if api_key:
        table.add_row(provider.env_var, mask_secret(api_key))
    if bot_token:
        table.add_row("TELEGRAM_BOT_TOKEN", mask_secret(bot_token))
    if allowed_users:
        table.add_row("TELEGRAM_ALLOWED_USERS", ",".join(allowed_users))

    table.add_row("", "")
    table.add_row("file", "~/.argus/config.yaml")
    table.add_row("agent.default_provider", provider_id)
    table.add_row("agent.default_model", model_id)
    table.add_row("agent.toolsets", "[memory, web, files, shell]")
    table.add_row("platforms.enabled", "[" + ", ".join(selected_platforms or []) + "]")
    if bot_token:
        table.add_row("gateway.telegram.enabled", "true")
        table.add_row("gateway.telegram.reactions", "true")
        table.add_row("gateway.telegram.forward_voice", "true")

    console.print(table)
