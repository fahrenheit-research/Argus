"""Real LLM provider transports.

The agent core talks to one abstract `ProviderTransport`. Concrete classes
wrap each vendor's official SDK (`openai`, `anthropic`, `groq`, …) and
translate to a single internal Delta stream so the chat REPL, Telegram
adapter, and agent loop never have to know which vendor is on the other
end of the wire.
"""

from argus.providers.base import (
    Delta,
    ProviderError,
    ProviderTransport,
    ToolCall,
    ToolSpec,
)
from argus.providers.registry import (
    available_transports,
    get_transport,
    transport_for,
)

__all__ = [
    "Delta",
    "ProviderError",
    "ProviderTransport",
    "ToolCall",
    "ToolSpec",
    "available_transports",
    "get_transport",
    "transport_for",
]
