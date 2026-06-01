"""Factory — give it a provider id + config, get a live transport.

Knows which concrete class implements which provider, what the env-var
holding the API key is called, what the default base URL is, and which
embedding model is sensible.

Lifecycle: callers must `await transport.aclose()` when done, or use
`transport_for(...)` inside an `async with` (it's an asynccontextmanager).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from argus.config import Config, resolve_secret
from argus.data.providers import get_provider
from argus.providers.anthropic import AnthropicTransport
from argus.providers.base import ProviderError, ProviderTransport
from argus.providers.openai_compatible import OpenAICompatibleTransport


@dataclass(frozen=True)
class _Spec:
    factory: str       # "openai_compat" | "anthropic"
    embedding_model: str | None = None


# Mapping of provider id -> how to build a transport for it.
# Anthropic gets the native client (prompt caching, etc.). Everyone else
# rides the OpenAI-compatible class.
_SPECS: dict[str, _Spec] = {
    "groq":        _Spec("openai_compat", embedding_model=None),
    "openai":      _Spec("openai_compat", embedding_model="text-embedding-3-small"),
    "anthropic":   _Spec("anthropic"),
    "openrouter":  _Spec("openai_compat", embedding_model=None),
    "gemini":      _Spec("openai_compat", embedding_model="text-embedding-004"),
    "deepseek":    _Spec("openai_compat", embedding_model=None),
    "mistral":     _Spec("openai_compat", embedding_model="mistral-embed"),
    "huggingface": _Spec("openai_compat", embedding_model=None),
    "ollama":      _Spec("openai_compat", embedding_model="nomic-embed-text"),
    "custom":      _Spec("openai_compat", embedding_model=None),
}


def available_transports() -> list[str]:
    return list(_SPECS.keys())


def get_transport(provider_id: str, cfg: Config | None = None) -> ProviderTransport:
    """Build a transport for `provider_id`, pulling the key from cfg/env.

    Raises ProviderError(kind="auth") if no key is found (other than for
    local providers like Ollama).
    """
    if provider_id not in _SPECS:
        raise ProviderError(f"unknown provider: {provider_id}", kind="not_found")
    spec = _SPECS[provider_id]
    info = get_provider(provider_id)

    # Determine effective base URL: user override > provider default.
    base_url = info.base_url
    if cfg and provider_id in cfg.providers and cfg.providers[provider_id].base_url:
        base_url = cfg.providers[provider_id].base_url

    # Resolve secret. Local providers (Ollama) may not have a key.
    api_key = resolve_secret(info.env_var, cfg) or ""
    if info.auth == "api_key" and not api_key:
        raise ProviderError(
            f"no API key set for {info.label} — run `argus setup` or `argus model`",
            kind="auth",
        )

    if spec.factory == "anthropic":
        return AnthropicTransport(api_key=api_key, base_url=base_url)
    return OpenAICompatibleTransport(
        id=info.id,
        label=info.label,
        base_url=base_url,
        api_key=api_key,
        embedding_model=spec.embedding_model,
    )


@asynccontextmanager
async def transport_for(provider_id: str, cfg: Config | None = None) -> AsyncIterator[ProviderTransport]:
    t = get_transport(provider_id, cfg)
    try:
        yield t
    finally:
        await t.aclose()
