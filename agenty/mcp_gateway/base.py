"""Composable MCP gateway used by orchestration workflow."""

from __future__ import annotations

from typing import Any, Protocol

from agenty.orchestration.tracing import trace_event


class MCPToolServer(Protocol):
    def list_tool_specs(self) -> list[dict[str, Any]]:
        ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        ...


class MCPGateway:
    def __init__(self, servers: list[MCPToolServer]) -> None:
        self._servers = servers
        self._tool_to_server: dict[str, MCPToolServer] = {}
        for server in servers:
            for spec in server.list_tool_specs():
                name = str(spec.get("name", "")).strip()
                if name:
                    self._tool_to_server[name] = server

    def list_tool_specs(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for server in self._servers:
            tools.extend(server.list_tool_specs())
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        server = self._tool_to_server.get(name)
        if server is None:
            raise KeyError(f"Unknown MCP tool: {name}")
        trace_event("mcp.gateway.dispatch", tool=name, server=server.__class__.__name__)
        return server.call_tool(name, arguments)
