"""Active platform context — lets tools know how their output should land.

When a tool runs in Telegram, it can use this contextvar to push artifacts
directly into the chat (send_voice / send_audio / send_document / send_photo)
instead of just writing them to disk and hoping someone delivers them.

When a tool runs in the CLI, the contextvar is None and tools fall back
to their default behaviour (write to ~/argus-workspace, print path, etc.).

Production-grade contract:
  - Set in `argus/platforms/telegram.py:_handle_message` before run_turn().
  - Set in `argus/screens/chat.py:run` to platform="cli" for completeness.
  - Read in any tool handler via get_platform_context().
  - Always None-safe — tools must check before using bot/chat_id.

Concurrency: a ContextVar is task-local under asyncio, so two concurrent
chats with different chat_ids do NOT cross-pollute. This is exactly what
asyncio's structured concurrency model is for.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class PlatformContext:
    """Where the agent is currently talking from."""
    platform: str          # "telegram" | "cli" | "api"
    bot: Any = None         # telegram.Bot when platform="telegram"
    chat_id: Optional[int] = None
    user_id: Optional[int] = None
    is_voice_input: bool = False     # user sent a voice note this turn

    @property
    def supports_audio_send(self) -> bool:
        """True if we can ship audio bytes straight into the active surface."""
        return self.platform == "telegram" and self.bot is not None and self.chat_id is not None

    @property
    def supports_file_send(self) -> bool:
        """True if we can deliver any file (document/pdf/xlsx) directly."""
        return self.platform == "telegram" and self.bot is not None and self.chat_id is not None


_CTX: ContextVar[Optional[PlatformContext]] = ContextVar("argus_platform_ctx", default=None)


def set_platform_context(ctx: PlatformContext) -> None:
    """Pin the active platform context for this asyncio Task."""
    _CTX.set(ctx)


def get_platform_context() -> Optional[PlatformContext]:
    """Return the active platform context, or None if running outside one."""
    return _CTX.get()


def clear_platform_context() -> None:
    _CTX.set(None)
