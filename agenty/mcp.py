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


class StaticMCPProvider:
    """Simple in-process MCP provider backed by Python callables."""

    def __init__(self, tools: dict[str, tuple[dict[str, Any], Any]]) -> None:
        self._tools = tools

    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [spec for spec, _handler in self._tools.values()]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in self._tools:
            raise KeyError(f"Unknown MCP tool: {name}")
        _spec, handler = self._tools[name]
        return handler(arguments)
