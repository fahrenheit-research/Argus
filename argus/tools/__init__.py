"""Built-in tool catalogue. Each tool is a small async function plus a
ToolSpec describing its JSON schema. The agent loop binds them by name.

To add a new tool: write the function, declare its spec in `register()`,
and add the category to argus/banner.py's tools list. The toolset gates
(PRD §14.1 `agent.toolsets`) gate which functions are exposed per turn.
"""

from argus.tools.registry import (
    ToolImpl,
    available_tools,
    get_tool,
    register_defaults,
    tools_for,
)

__all__ = ["ToolImpl", "available_tools", "get_tool", "register_defaults", "tools_for"]
