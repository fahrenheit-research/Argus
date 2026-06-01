"""OpenAI-compatible transport — covers Groq, OpenAI, OpenRouter, DeepSeek,
Ollama, Hugging Face Inference Providers, and any custom OpenAI-shaped
endpoint (vLLM, LM Studio, LiteLLM, …).

These five+ providers all speak the OpenAI Chat Completions wire format
with `tools`/`tool_calls`. Differences are mostly base_url, model lists,
and the auth header (Bearer for everyone — even Ollama tolerates the
header). One class, parameterised on construction.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
from openai import APIError, APITimeoutError, AsyncOpenAI, AuthenticationError, RateLimitError

from argus.providers.base import (
    Delta,
    Message,
    ProviderError,
    ProviderTransport,
    ToolCall,
    ToolSpec,
)

# Map the rich Hermes FailoverReason enum onto our 6-case ProviderError.kind.
# (We keep both: the kind is the public contract the loop's fallback chain
# branches on; the FailoverReason is logged and exposed for telemetry.)
_HERMES_TO_KIND = {
    "auth": "auth",
    "auth_permanent": "auth",
    "billing": "auth",
    "rate_limit": "rate_limit",
    "overloaded": "rate_limit",
    "server_error": "server",
    "timeout": "network",
    "network": "network",
    "context_overflow": "server",
    "payload_too_large": "server",
    "model_not_found": "not_found",
    "content_policy_blocked": "other",
    "provider_policy_blocked": "other",
    "format_error": "other",
    "bad_tool_call": "other",
    "unknown": "other",
}


class OpenAICompatibleTransport(ProviderTransport):
    """A single transport that talks the OpenAI Chat Completions dialect.

    Instantiated with the provider's id/label/base_url/api_key/embedding
    model so we can reuse the class for every OAI-shaped backend."""

    def __init__(
        self,
        *,
        id: str,
        label: str,
        base_url: str,
        api_key: str = "",
        request_timeout: float = 60.0,
        embedding_model: str | None = None,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, request_timeout=request_timeout)
        self.id = id
        self.label = label
        self.embedding_model = embedding_model
        # Ollama and other local endpoints don't require a key; pass a
        # placeholder so the SDK doesn't refuse to construct.
        self._client = AsyncOpenAI(
            api_key=api_key or "ollama-local",
            base_url=base_url,
            timeout=request_timeout,
            max_retries=0,  # we own retries via the agent loop / tenacity
        )

    # ----- streaming completion --------------------------------------------

    async def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Delta]:
        # Bookkeeping that must exist before any yield so the post-stream
        # emission logic doesn't trip on a NameError if the create() call
        # blows up before assignment.
        partial_calls: dict[int, dict[str, Any]] = {}
        usage_in = usage_out = 0
        finish_reason = ""
        stream = None

        from argus.hermes.sanitize import sanitize_messages_surrogates

        wire_messages = _to_openai_messages(messages)
        if sanitize_messages_surrogates(wire_messages):
            # Lone surrogates would have crashed json.dumps inside the
            # SDK. We scrubbed; log so a metrics dashboard could pick this up.
            import logging
            logging.getLogger("argus.providers").warning(
                "scrubbed surrogate chars from outbound %s messages", self.label
            )

        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=wire_messages,
                tools=_to_openai_tools(tools) if tools else None,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )

            # Consume the stream INSIDE the try so mid-stream APIErrors
            # (Groq sometimes raises "Failed to call a function" here)
            # get classified the same way as create() errors.
            async for chunk in stream:
                if chunk.usage:
                    usage_in = chunk.usage.prompt_tokens or 0
                    usage_out = chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                if delta.content:
                    yield Delta(kind="text", text=delta.content)

                # Tool calls arrive in fragments (id first, then name,
                # then arguments piece-by-piece). Buffer until finish.
                for tc in delta.tool_calls or []:
                    slot = partial_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

        except (AuthenticationError, RateLimitError, APITimeoutError, APIError) as e:
            # Go through the real Hermes-style classifier so the loop
            # gets accurate recovery hints (should_compress, should_fallback,
            # should_rotate_credential, retry_after_seconds) instead of a
            # 6-case enum guess. We pass the ClassifiedError through so the
            # loop can branch on FailoverReason directly.
            from argus.hermes.error_classifier import classify_api_error

            ce = classify_api_error(e, provider=self.id, model=model)
            raise ProviderError(
                f"{self.label}: {ce.reason.value}: {ce.detail[:200]}",
                kind=_HERMES_TO_KIND.get(ce.reason.value, "other"),
                retry_after=ce.retry_after_seconds,
                classified=ce,
            ) from e
        except Exception as e:  # noqa: BLE001 — last-resort
            from argus.hermes.error_classifier import classify_api_error

            ce = classify_api_error(e, provider=self.id, model=model)
            raise ProviderError(
                f"{self.label}: {ce.reason.value}: {ce.detail[:200]}",
                kind=_HERMES_TO_KIND.get(ce.reason.value, "other"),
                classified=ce,
            ) from e

        # ----- emit completed tool calls -----------------------------------
        # Use the Hermes truncation detection + repair helpers — catches
        # the case where OpenRouter rewrites finish_reason from `length`
        # to `tool_calls` mid-stream, leaving us with half-baked JSON.
        from argus.hermes.sanitize import looks_truncated, try_repair_tool_args

        for slot in partial_calls.values():
            if looks_truncated(slot["args"]):
                import logging
                logging.getLogger("argus.providers").warning(
                    "tool-call args look truncated mid-stream for %s: %r",
                    slot["name"], slot["args"][-80:],
                )
            args, repaired = try_repair_tool_args(slot["args"])
            if repaired:
                import logging
                logging.getLogger("argus.providers").info(
                    "repaired tool-call args for %s", slot["name"],
                )
            yield Delta(
                kind="tool_call",
                tool_call=ToolCall(id=slot["id"], name=slot["name"], arguments=args),
            )

        yield Delta(
            kind="finish",
            finish_reason=finish_reason or "stop",
            tokens_in=usage_in,
            tokens_out=usage_out,
        )

    # ----- validate / list ---------------------------------------------------

    async def validate_key(self) -> tuple[bool, str]:
        try:
            resp = await self._client.models.list()
            n = len(list(resp.data))
            return True, f"{self.label}: {n} model(s) available"
        except AuthenticationError as e:
            return False, f"{self.label}: invalid API key ({e})"
        except APIError as e:
            return False, f"{self.label}: API error {e.status_code or '?'}"
        except (httpx.HTTPError, OSError) as e:
            return False, f"{self.label}: network error ({e})"

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.models.list()
            return sorted(m.id for m in resp.data)
        except (APIError, AuthenticationError):
            return []

    # ----- embeddings --------------------------------------------------------

    async def embed(self, *, model: str, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        try:
            resp = await self._client.embeddings.create(model=model, input=inputs)
            return [item.embedding for item in resp.data]
        except AuthenticationError as e:
            raise ProviderError(f"{self.label}: invalid API key for embeddings", kind="auth") from e
        except APIError as e:
            raise ProviderError(f"{self.label}: embeddings error {e.status_code or '?'}: {e.message}",
                                kind="server" if (e.status_code or 0) >= 500 else "other") from e

    async def aclose(self) -> None:
        await self._client.close()


# ---------------------------------------------------------------------------
# Wire-format translators
# ---------------------------------------------------------------------------


def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            continue
        msg: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ]
            # When the assistant turn is purely tool calls, content can be empty.
            msg["content"] = msg["content"] or None
        out.append(msg)
    return out


def _to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "strict": t.strict,
                },
            }
        )
    return out


def _retry_after_from_exc(e: Exception) -> float | None:
    """Best-effort retry-after extraction from a RateLimitError."""
    try:
        resp = getattr(e, "response", None)
        if resp is None:
            return None
        ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        return float(ra) if ra else None
    except (ValueError, AttributeError):
        return None
