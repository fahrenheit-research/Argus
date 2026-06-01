"""
AgentWire v0.3 Decoder
Supports Standard, Champion, and Champion Binary profiles.
Created by Fahrenheit Research and made open source.
"""
import json
import struct
from typing import Any, Dict, Union
from .envelope import MessageEnvelope, ErrorEnvelope


def decode(message: Union[str, bytes]) -> Dict[str, Any]:
    """
    Universal decoder for all AgentWire profiles.
    """
    if isinstance(message, bytes):
        return _decode_binary(message)
    else:
        return _decode_json(message)


def _decode_json(message: str) -> Dict[str, Any]:
    obj = json.loads(message)
    header = obj.get("header", {})
    body = obj.get("body", {})

    profile = header.get("profile", "standard")

    if profile == "champion":
        keys = header.get("keys", [])
        decoded_body = {keys[i]: body[i] for i in range(len(keys))}
    else:
        decoded_body = body

    result = {
        "header": header,
        "body": decoded_body
    }

    # Attach error envelope if present
    if "error" in obj:
        result["error"] = ErrorEnvelope(**obj["error"])

    return result


def _decode_binary(data: bytes) -> Dict[str, Any]:
    """Decode Champion Binary format."""
    header_len = struct.unpack(">I", data[0:4])[0]
    header_bytes = data[4:4+header_len]
    body_start = 4 + header_len
    body_len = struct.unpack(">I", data[body_start:body_start+4])[0]
    body_bytes = data[body_start+4:body_start+4+body_len]

    header = json.loads(header_bytes.decode("utf-8"))
    body = json.loads(body_bytes.decode("utf-8"))

    return {
        "header": header,
        "body": body
    }
