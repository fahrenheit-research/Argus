"""ARGUS third-party service connectors.

Each connector provides:
  - metadata: name, icon, description, auth type
  - setup():  interactive CLI wizard that opens a browser and captures OAuth
  - test():   async function that proves the connection works
  - mcp_config(): dict that can be saved to ~/.argus/mcp_config.json

Connectors are auto-discovered by name. Natural-language triggers such as
"connect gmail" or "link my twitter" are detected in the REPL and route
here before the message reaches the LLM.
"""

from argus.connectors.registry import (
    CONNECTORS,
    detect_connect_intent,
    get_connector,
    list_connectors,
    run_connect_wizard,
)

__all__ = [
    "CONNECTORS",
    "detect_connect_intent",
    "get_connector",
    "list_connectors",
    "run_connect_wizard",
]
