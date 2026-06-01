"""
AgentWire v0.3 Encoder
Includes Standard, Champion (JSON), and Champion Binary profiles.
Created by Fahrenheit Research and made open source.
"""
import json
import struct
from typing import Any, Dict
from .envelope import MessageEnvelope

def encode(data: Dict[str, Any], envelope: MessageEnvelope = None) -> str:
    """Standard profile - human readable."""
    env = envelope or MessageEnvelope(profile="standard")
    payload = {
        "header": env.to_dict(),
        "body": data
    }
    return json.dumps(payload, separators=(",", ":"))


def encode_champion(data: Dict[str, Any], envelope: MessageEnvelope = None) -> str:
    """Champion profile (JSON-based positional)."""
    env = envelope or MessageEnvelope(profile="champion")
    keys = list(data.keys())
    values = [data[k] for k in keys]
    env.profile = "champion"
    payload = {
        "header": {**env.to_dict(), "keys": keys},
        "body": values
    }
    return json.dumps(payload, separators=(",", ":"))


def encode_champion_binary(data: Dict[str, Any], envelope: MessageEnvelope = None) -> bytes:
    """
    Champion Binary profile (v0.3)
    Simple but efficient binary format:
    - Header length (4 bytes)
    - Header (JSON)
    - Body length (4 bytes)
    - Body (MessagePack-like simple encoding or JSON for compatibility)
    For maximum efficiency we use a compact binary representation.
    """
    env = envelope or MessageEnvelope(profile="champion-binary")
    env.profile = "champion-binary"

    header = env.to_dict()
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")

    # For body we use a very compact representation
    body_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")

    # Format: [header_len][header][body_len][body]
    packet = struct.pack(">I", len(header_bytes)) + header_bytes + \
             struct.pack(">I", len(body_bytes)) + body_bytes

    return packet
