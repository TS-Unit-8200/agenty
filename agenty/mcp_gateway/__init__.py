"""MCP gateway and built-in adapters for orchestration."""

from agenty.mcp_gateway.base import MCPGateway
from agenty.mcp_gateway.comms_mock import CommsMockMCPServer
from agenty.mcp_gateway.phone_call import PhoneCallMCPServer
from agenty.mcp_gateway.resource_crud import ResourceCrudMCPServer
from agenty.mcp_gateway.scenario_gen import ScenarioGenMCPServer

__all__ = [
    "CommsMockMCPServer",
    "MCPGateway",
    "PhoneCallMCPServer",
    "ResourceCrudMCPServer",
    "ScenarioGenMCPServer",
]
