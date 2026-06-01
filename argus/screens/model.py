"""`argus model` — provider + model picker."""

from __future__ import annotations

from argus import picker
from argus.data.providers import PROVIDERS, get_provider
from argus.screens._common import hint, kv_line, ok_panel, screen_header
from argus.theme import console as new_console


def run(
    current_provider: str = "groq",
    current_model: str = "llama-3.3-70b-versatile",
) -> tuple[str | None, str | None]:
    """Returns (new_provider, new_model). Either may be None if unchanged."""
    console = new_console()
    screen_header(console, "Model & provider", subtitle="pick a default for this session and forward.")

    console.print(kv_line("current provider", current_provider))
    console.print(kv_line("current model", current_model))
    console.print()

    provider_id = picker.select(
        "provider",
        choices=[
            picker.Choice(value=p.id, label=p.label, description=p.blurb)
            for p in PROVIDERS
        ],
        default=current_provider,
        current=current_provider,
    )
    if not provider_id:
        return None, None
    provider = get_provider(provider_id)
    console.print()

    model_id = picker.select(
        "model",
        choices=[
            picker.Choice(value=m.id, label=m.id, description=m.description)
            for m in provider.models
        ],
        default=current_model if current_model in {m.id for m in provider.models} else (provider.models[0].id if provider.models else None),
        current=current_model if provider_id == current_provider else None,
    )
    if not model_id:
        return provider_id, None

    ok_panel(
        console,
        f"Default set to {provider.label} · {model_id}",
        sub="Use /model in chat to switch again any time.",
    )
    return provider_id, model_id
