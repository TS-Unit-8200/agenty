"""Extension points for MCP (Model Context Protocol) tool servers.

Concrete MCP clients will plug in here later without changing session flow.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MCPProvider(Protocol):
    """Optional provider that can list tools and execute them by name."""

    def list_tool_specs(self) -> list[dict[str, Any]]:
        """Return JSON-schema style tool definitions for the chat API (future)."""
        ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool and return a string result for the model."""
        ...
