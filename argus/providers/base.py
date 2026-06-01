"""ProviderTransport ABC — the contract every LLM backend implements.

Design notes:
- All methods are async. The CLI/REPL adapts via asyncio.run().
- Streaming yields a unified `Delta` (text | tool_call | finish) so callers
  never branch on provider type.
- Tool schemas are defined in one place (ToolSpec) and translated per
  provider at call time, mirroring PRD §11 and Hermes-Agent's pattern.
- `validate_key()` is the cheapest possible health probe — it MUST NOT
  consume completion tokens. Used during `argus setup` and `argus doctor`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal


# ---------------------------------------------------------------------------
# Tool schema (internal, vendor-neutral)
# ---------------------------------------------------------------------------


@dataclass
class ToolSpec:
    """Vendor-neutral tool description. Each transport translates to its
    own wire format (OpenAI `tools`, Anthropic `tools`, etc.)."""
    name: str
    description: str
    parameters: dict[str, Any]   # JSON Schema for the args object
    strict: bool = False         # OpenAI strict mode hint


@dataclass
class ToolCall:
    id: str                       # vendor-issued call id
    name: str
    arguments: dict[str, Any]     # parsed JSON args (or {} if model gave none)


# ---------------------------------------------------------------------------
# Unified streaming Delta
# ---------------------------------------------------------------------------


@dataclass
class Delta:
    kind: Literal["text", "tool_call", "finish", "error"]
    text: str = ""
    tool_call: ToolCall | None = None
    finish_reason: str = ""
    error: str = ""
    # Optional usage info, populated on `finish` for cost/budget tracking.
    tokens_in: int = 0
    tokens_out: int = 0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Raised when a transport can't satisfy a request.

    Carries:
      - `kind`: the 6-case enum the agent loop's fallback chain branches on
      - `retry_after`: provider-supplied delay hint in seconds (429s)
      - `classified`: full Hermes-style ClassifiedError with recovery hints
        (`should_compress`, `should_fallback`, `should_rotate_credential`,
        plus the precise FailoverReason). The loop reads this to decide
        whether to drop tools, compress context, fall back, etc. — none
        of which we can express with `kind` alone.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["auth", "rate_limit", "not_found", "server", "network", "other"] = "other",
        retry_after: float | None = None,
        classified: Any = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.classified = classified  # type: argus.hermes.error_classifier.ClassifiedError | None


# ---------------------------------------------------------------------------
# Abstract transport
# ---------------------------------------------------------------------------


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    name: str = ""                          # tool name when role=="tool"
    tool_call_id: str = ""                  # tool message reply correlation
    tool_calls: list[ToolCall] = field(default_factory=list)  # assistant w/ calls


class ProviderTransport(abc.ABC):
    """One concrete subclass per wire format. Configured with the user's
    API key (or local URL) and a default model."""

    id: str          # e.g. "groq"
    label: str       # e.g. "Groq"
    base_url: str    # used for both completions and embeddings

    def __init__(self, *, api_key: str = "", base_url: str = "", request_timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.base_url = base_url or self.base_url
        self.request_timeout = request_timeout

    # ----- core async API ----------------------------------------------------

    @abc.abstractmethod
    async def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Delta]:
        """Yield deltas as the model speaks."""

    @abc.abstractmethod
    async def validate_key(self) -> tuple[bool, str]:
        """Cheap health probe. Returns (ok, human-readable detail).
        MUST NOT consume completion tokens — use /models or equivalent."""

    @abc.abstractmethod
    async def list_models(self) -> list[str]:
        """Return the list of model IDs available with the current key.
        Used during setup wizard's model picker and by `argus doctor`."""

    async def embed(self, *, model: str, inputs: list[str]) -> list[list[float]]:
        """Compute embeddings for `inputs`. Default raises — only
        providers with embedding endpoints override this."""
        raise ProviderError(
            f"{self.label} has no embedding endpoint — configure auxiliary.embedding to a provider that does.",
            kind="not_found",
        )

    # ----- helpers ----------------------------------------------------------

    async def aclose(self) -> None:
        """Override to release per-transport resources (httpx clients, etc.)."""
        return None
