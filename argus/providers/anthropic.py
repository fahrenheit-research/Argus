"""Anthropic transport — native `messages` API with prompt-caching support.

Anthropic's wire format differs from OpenAI's in three ways that matter:

1. `system` is a top-level field, not a message
2. `tools` use input_schema instead of `parameters`; tool replies are
   user-role messages with a `tool_result` content block
3. Prompt caching is opt-in via `cache_control: { type: "ephemeral" }` on
   the system/tools/conversation blocks we want cached
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from anthropic import (
    APIError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError,
    RateLimitError,
)

from argus.providers.base import (
    Delta,
    Message,
    ProviderError,
    ProviderTransport,
    ToolCall,
    ToolSpec,
)


class AnthropicTransport(ProviderTransport):
    id = "anthropic"
    label = "Anthropic"
    base_url = "https://api.anthropic.com"

    def __init__(self, *, api_key: str = "", base_url: str = "", request_timeout: float = 60.0) -> None:
        super().__init__(api_key=api_key, base_url=base_url, request_timeout=request_timeout)
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=self.base_url or None,
            timeout=request_timeout,
            max_retries=0,
        )

    async def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Delta]:
        from argus.hermes.prompt_caching import apply_anthropic_cache_control
        from argus.hermes.sanitize import sanitize_messages_surrogates

        system, convo = _split_system(messages)
        # Apply the system_and_3 cache strategy (system prompt + last 3
        # non-system messages all get cache breakpoints). 90% read discount
        # on cache hits — pays for itself in 2 turns.
        convo = apply_anthropic_cache_control(convo, cache_ttl="5m", native_anthropic=True)
        if sanitize_messages_surrogates(convo):
            import logging
            logging.getLogger("argus.providers").warning(
                "scrubbed surrogates from outbound Anthropic messages"
            )

        try:
            async with self._client.messages.stream(
                model=model,
                max_tokens=max_tokens or 4096,
                temperature=temperature,
                system=_with_cache(system) if system else None,
                tools=_to_anthropic_tools(tools) if tools else None,
                messages=convo,
            ) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield Delta(kind="text", text=event.text)
                    elif event.type == "content_block_stop" and getattr(event, "content_block", None):
                        block = event.content_block
                        if getattr(block, "type", "") == "tool_use":
                            yield Delta(
                                kind="tool_call",
                                tool_call=ToolCall(
                                    id=block.id,
                                    name=block.name,
                                    arguments=dict(block.input or {}),
                                ),
                            )
                final = await stream.get_final_message()
                yield Delta(
                    kind="finish",
                    finish_reason=str(final.stop_reason or "stop"),
                    tokens_in=final.usage.input_tokens,
                    tokens_out=final.usage.output_tokens,
                )
        except AuthenticationError as e:
            raise ProviderError(f"Anthropic: invalid API key ({e})", kind="auth") from e
        except RateLimitError as e:
            raise ProviderError("Anthropic: rate limited", kind="rate_limit") from e
        except APITimeoutError as e:
            raise ProviderError("Anthropic: request timed out", kind="network") from e
        except APIError as e:
            raise ProviderError(
                f"Anthropic: API error {e.status_code or '?'}: {e.message}",
                kind="server" if (e.status_code or 0) >= 500 else "other",
            ) from e

    async def validate_key(self) -> tuple[bool, str]:
        # Anthropic now exposes /models — free, no token cost.
        try:
            resp = await self._client.models.list()
            n = len(list(resp.data))
            return True, f"Anthropic: {n} model(s) available"
        except AuthenticationError:
            return False, "Anthropic: invalid API key"
        except APIError as e:
            # Some regions still 404 the models endpoint — fall back to a
            # single-token ping that costs ~$0.000001.
            try:
                await self._client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=1,
                    messages=[{"role": "user", "content": "ping"}],
                )
                return True, "Anthropic: key valid (via 1-token ping)"
            except AuthenticationError:
                return False, "Anthropic: invalid API key"
            except APIError as e2:
                return False, f"Anthropic: API error {e2.status_code or '?'}"

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.models.list()
            return sorted(m.id for m in resp.data)
        except (APIError, AuthenticationError):
            # Static fallback from docs.anthropic.com/en/docs/about-claude/models.
            return ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"]

    async def aclose(self) -> None:
        await self._client.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_system(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    sys = ""
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            sys += (m.content + "\n")
            continue
        if m.role == "tool":
            # Anthropic represents tool results as user-role messages with
            # a `tool_result` content block.
            rest.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}],
            })
            continue
        if m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            rest.append({"role": "assistant", "content": blocks})
            continue
        rest.append({"role": m.role, "content": m.content})
    return sys.strip(), rest


def _to_anthropic_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
            # Mark every tool def as cacheable — Anthropic charges 10% for
            # cache hits vs full read cost, dwarfing the storage write.
            "cache_control": {"type": "ephemeral"},
        }
        for t in tools
    ]


def _with_cache(system: str) -> list[dict[str, Any]]:
    """Wrap the system prompt as a single cacheable text block."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
