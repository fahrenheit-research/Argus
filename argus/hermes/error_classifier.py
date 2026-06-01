"""API error classifier — port of `hermes-agent/agent/error_classifier.py`.

Hermes' full classifier is ~1500 lines covering 30+ failure modes with
priority-ordered pipeline (HTTP status → error code → message regex →
provider-specific quirks). This port covers the 15 cases that matter for
ARGUS's provider matrix (Groq, OpenAI, Anthropic, OpenRouter, DeepSeek,
HF, Ollama, custom OAI-compat) and leaves seams for the rest.

The contract: every API error becomes a `ClassifiedError` with a reason
and three recovery hints. The agent loop branches on the hints, not on
exception types or message regex — that's what makes this composable.
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("argus.classifier")


class FailoverReason(enum.Enum):
    # Authentication
    auth = "auth"                                # 401/403, transient — refresh/rotate
    auth_permanent = "auth_permanent"            # auth failed after refresh — abort

    # Billing / quota
    billing = "billing"                          # 402 or credit exhaustion — rotate
    rate_limit = "rate_limit"                    # 429 — backoff then rotate

    # Server-side
    overloaded = "overloaded"                    # 503 / 529 — backoff
    server_error = "server_error"                # 500 / 502 — retry

    # Transport
    timeout = "timeout"                          # connection or read timeout
    network = "network"                          # DNS / TCP / TLS

    # Payload / context
    context_overflow = "context_overflow"        # too many tokens — compress
    payload_too_large = "payload_too_large"      # 413 — shrink payload

    # Model / provider policy
    model_not_found = "model_not_found"          # 404 — fallback model
    content_policy_blocked = "content_policy_blocked"  # safety reject — don't retry as-is
    provider_policy_blocked = "provider_policy_blocked"  # aggregator privacy block

    # Request format
    format_error = "format_error"                # 400 bad request — abort or strip & retry
    bad_tool_call = "bad_tool_call"              # model emitted malformed function call

    # Unknown
    unknown = "unknown"                          # default — retryable=True


@dataclass
class ClassifiedError:
    reason: FailoverReason
    retryable: bool = False
    should_rotate_credential: bool = False
    should_fallback: bool = False
    should_compress: bool = False
    retry_after_seconds: float | None = None
    detail: str = ""

    @property
    def is_auth(self) -> bool:
        return self.reason in (FailoverReason.auth, FailoverReason.auth_permanent)

    @property
    def is_billing(self) -> bool:
        return self.reason == FailoverReason.billing

    @property
    def is_transient(self) -> bool:
        return self.reason in (
            FailoverReason.rate_limit,
            FailoverReason.overloaded,
            FailoverReason.server_error,
            FailoverReason.timeout,
            FailoverReason.network,
        )


# ── String pattern groups (kept simple — Hermes ships ~15 of these) ──────

_BILLING_PATTERNS = (
    "insufficient credit",
    "insufficient_quota",
    "quota exceeded",
    "exceeded your current quota",
    "billing",
    "no credit",
    "payment required",
    "subscription",
)

_RATE_LIMIT_PATTERNS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "tokens per minute",
    "tpm",
    "rpm",
    "throttled",
)

_PAYLOAD_TOO_LARGE_PATTERNS = (
    "request too large",
    "payload too large",
    "exceeds the maximum",
    "context length",
    "maximum context length",
    "context_length_exceeded",
    "string too long",
)

_BAD_TOOL_CALL_PATTERNS = (
    "failed to call a function",
    "tool call",
    "function call",
    "tool_use_failed",
    "tool_calls",
    "invalid_tool",
    "tool name",
    "function name",
)

_CONTENT_POLICY_PATTERNS = (
    "content_policy",
    "content policy",
    "safety",
    "blocked by safety",
    "blocked by content filter",
    "responsible ai",
)

_TIMEOUT_PATTERNS = (
    "timed out",
    "timeout",
    "deadline exceeded",
)

_NETWORK_PATTERNS = (
    "connection",
    "dns",
    "ssl",
    "tls",
    "broken pipe",
    "reset by peer",
)

_MODEL_NOT_FOUND_PATTERNS = (
    "model not found",
    "no such model",
    "model_not_found",
    "does not exist",
    "decommissioned",
    "deprecated",
)


def _any(haystack: str, patterns: tuple[str, ...]) -> bool:
    h = haystack.lower()
    return any(p in h for p in patterns)


# ── Public entry point ──────────────────────────────────────────────────


def classify_api_error(
    error: BaseException,
    *,
    provider: str = "",
    model: str = "",
    approx_tokens: int | None = None,
) -> ClassifiedError:
    """Classify any provider exception into a `ClassifiedError`.

    Priority pipeline (Hermes pattern):
      1. exception type (OpenAI/Anthropic SDK classes with semantic meaning)
      2. HTTP status code (if attached)
      3. error body / message regex
      4. fallback: `unknown`, retryable=True
    """
    # ── 1. typed SDK exceptions ────────────────────────────────────────
    typed = _classify_by_type(error)
    if typed is not None:
        return typed

    # Extract message + status for the remaining layers.
    msg = str(error) or ""
    status = _http_status(error)

    # ── 2. HTTP status ─────────────────────────────────────────────────
    by_status = _classify_by_status(status, msg)
    if by_status is not None:
        return by_status

    # ── 3. message regex ───────────────────────────────────────────────
    by_msg = _classify_by_message(msg)
    if by_msg is not None:
        return by_msg

    # ── 4. fallback ────────────────────────────────────────────────────
    return ClassifiedError(
        reason=FailoverReason.unknown,
        retryable=True,
        detail=msg or type(error).__name__,
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _http_status(error: BaseException) -> int:
    """Best-effort: pull a status code off any of the common SDK exceptions."""
    for attr in ("status_code", "status", "code"):
        v = getattr(error, attr, None)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    resp = getattr(error, "response", None)
    if resp is not None:
        v = getattr(resp, "status_code", None)
        if isinstance(v, int):
            return v
    return 0


def _classify_by_type(error: BaseException) -> ClassifiedError | None:
    """Match against SDK-provided exception classes when their names are unambiguous."""
    name = type(error).__name__

    if name == "AuthenticationError":
        return ClassifiedError(
            reason=FailoverReason.auth,
            retryable=False,
            should_rotate_credential=True,
            detail=str(error),
        )

    if name == "RateLimitError":
        return ClassifiedError(
            reason=FailoverReason.rate_limit,
            retryable=True,
            should_fallback=True,
            retry_after_seconds=_retry_after(error),
            detail=str(error),
        )

    if name in ("APITimeoutError", "Timeout"):
        return ClassifiedError(
            reason=FailoverReason.timeout,
            retryable=True,
            detail=str(error),
        )

    if name in ("APIConnectionError", "ConnectionError"):
        return ClassifiedError(
            reason=FailoverReason.network,
            retryable=True,
            detail=str(error),
        )

    return None


def _classify_by_status(status: int, msg: str) -> ClassifiedError | None:
    if not status:
        return None

    if status == 401:
        return ClassifiedError(
            reason=FailoverReason.auth,
            retryable=False,
            should_rotate_credential=True,
            detail=msg,
        )
    if status == 402:
        return ClassifiedError(
            reason=FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            detail=msg,
        )
    if status == 403:
        # 403 splits between auth and policy block — peek at the body.
        if _any(msg, _CONTENT_POLICY_PATTERNS):
            return ClassifiedError(
                reason=FailoverReason.content_policy_blocked,
                retryable=False,
                detail=msg,
            )
        return ClassifiedError(
            reason=FailoverReason.auth,
            retryable=False,
            should_rotate_credential=True,
            detail=msg,
        )
    if status == 404:
        return ClassifiedError(
            reason=FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
            detail=msg,
        )
    if status == 413:
        return ClassifiedError(
            reason=FailoverReason.payload_too_large,
            retryable=True,
            should_compress=True,
            detail=msg,
        )
    if status == 429:
        return ClassifiedError(
            reason=FailoverReason.rate_limit,
            retryable=True,
            should_fallback=True,
            retry_after_seconds=_retry_after_from_msg(msg),
            detail=msg,
        )
    if status == 400:
        return _classify_400(msg)
    if status == 502:
        return ClassifiedError(
            reason=FailoverReason.server_error,
            retryable=True,
            should_fallback=True,
            detail=msg,
        )
    if status in (503, 529):
        return ClassifiedError(
            reason=FailoverReason.overloaded,
            retryable=True,
            detail=msg,
        )
    if 500 <= status < 600:
        return ClassifiedError(
            reason=FailoverReason.server_error,
            retryable=True,
            should_fallback=True,
            detail=msg,
        )
    return None


def _classify_400(msg: str) -> ClassifiedError:
    """400 Bad Request — split between bad-tool-call, payload-too-large,
    content-policy, and pure format errors."""
    if _any(msg, _BAD_TOOL_CALL_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.bad_tool_call,
            retryable=True,
            detail=msg,
        )
    if _any(msg, _PAYLOAD_TOO_LARGE_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.payload_too_large,
            retryable=True,
            should_compress=True,
            detail=msg,
        )
    if _any(msg, _CONTENT_POLICY_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.content_policy_blocked,
            retryable=False,
            detail=msg,
        )
    return ClassifiedError(
        reason=FailoverReason.format_error,
        retryable=False,
        detail=msg,
    )


def _classify_by_message(msg: str) -> ClassifiedError | None:
    low = msg.lower()
    # Bare HTTP-status strings like "401 Unauthorized" or "500 Internal".
    # In production these arrive as typed SDK exceptions, but logs and
    # tests sometimes see them as plain strings.
    if "401" in low or "unauthorized" in low:
        return ClassifiedError(
            reason=FailoverReason.auth,
            retryable=False,
            should_rotate_credential=True,
            detail=msg,
        )
    if "402" in low or "payment required" in low:
        return ClassifiedError(
            reason=FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            detail=msg,
        )
    if "403" in low or "forbidden" in low:
        return ClassifiedError(
            reason=FailoverReason.auth,
            retryable=False,
            should_rotate_credential=True,
            detail=msg,
        )
    if "500" in low or "internal server error" in low:
        return ClassifiedError(
            reason=FailoverReason.server_error,
            retryable=True,
            should_fallback=True,
            detail=msg,
        )
    if "503" in low or "service unavailable" in low or "529" in low:
        return ClassifiedError(
            reason=FailoverReason.overloaded,
            retryable=True,
            detail=msg,
        )
    if _any(msg, _BILLING_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            detail=msg,
        )
    if _any(msg, _RATE_LIMIT_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.rate_limit,
            retryable=True,
            should_fallback=True,
            retry_after_seconds=_retry_after_from_msg(msg),
            detail=msg,
        )
    if _any(msg, _PAYLOAD_TOO_LARGE_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.payload_too_large,
            retryable=True,
            should_compress=True,
            detail=msg,
        )
    if _any(msg, _BAD_TOOL_CALL_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.bad_tool_call,
            retryable=True,
            detail=msg,
        )
    if _any(msg, _CONTENT_POLICY_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.content_policy_blocked,
            retryable=False,
            detail=msg,
        )
    if _any(msg, _MODEL_NOT_FOUND_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
            detail=msg,
        )
    if _any(msg, _TIMEOUT_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.timeout,
            retryable=True,
            detail=msg,
        )
    if _any(msg, _NETWORK_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.network,
            retryable=True,
            detail=msg,
        )
    return None


def _retry_after(error: BaseException) -> float | None:
    """Pull `Retry-After` off the response if present."""
    resp = getattr(error, "response", None)
    if resp is None:
        return None
    headers = getattr(resp, "headers", None) or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


_RETRY_AFTER_RE = re.compile(r"retry[\s_-]*after[:= ]\s*([0-9.]+)\s*s", re.I)


def _retry_after_from_msg(msg: str) -> float | None:
    m = _RETRY_AFTER_RE.search(msg)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


__all__ = ["FailoverReason", "ClassifiedError", "classify_api_error"]
