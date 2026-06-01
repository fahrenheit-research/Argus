"""AgentWire (vendored) — message envelope + serialization.

Source: https://github.com/fahrenheit-research/AgentWire
License: bundled under the same terms as the upstream repo.

Used inside ARGUS for:
- per-turn correlation IDs across the agent loop, provider calls, and
  Telegram message edits (so a single user message can be traced through
  the whole pipeline by its `correlation_id`)
- compact serialization of tool-call results when they're persisted
"""

from argus.agentwire.envelope import ErrorEnvelope, MessageEnvelope
from argus.agentwire.encoder import encode, encode_champion
from argus.agentwire.decoder import decode

__all__ = [
    "encode",
    "encode_champion",
    "decode",
    "MessageEnvelope",
    "ErrorEnvelope",
]
