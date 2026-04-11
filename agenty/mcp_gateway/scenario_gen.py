"""MCP scenario helper tools for deterministic scoring and comparison."""

from __future__ import annotations

import json
from typing import Any

from agenty.orchestration.tracing import trace_event


class ScenarioGenMCPServer:
    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [
            {"name": "scenario_risk_score", "description": "Compute risk score for scenario", "input_schema": {"type": "object", "properties": {"risks": {"type": "array", "items": {"type": "string"}}, "priority": {"type": "string"}}, "required": ["risks", "priority"]}},
            {"name": "scenario_estimate_cost", "description": "Estimate coarse scenario cost", "input_schema": {"type": "object", "properties": {"affected_population": {"type": "integer"}, "resource_count": {"type": "integer"}}, "required": ["affected_population", "resource_count"]}},
            {"name": "scenario_compare", "description": "Compare scenario options by score", "input_schema": {"type": "object", "properties": {"scenarios": {"type": "array"}}, "required": ["scenarios"]}},
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        trace_event("mcp.scenario.request", tool=name)
        if name == "scenario_risk_score":
            risks = list(arguments.get("risks", []))
            priority = str(arguments.get("priority", "medium")).lower()
            multiplier = 1.5 if priority in {"critical", "krytyczny"} else 1.2 if priority in {"high", "wysoki"} else 1.0
            score = round(len(risks) * 10 * multiplier, 2)
            result = json.dumps({"score": score})
            trace_event("mcp.scenario.response", tool=name, result=result)
            return result

        if name == "scenario_estimate_cost":
            population = int(arguments.get("affected_population", 0))
            resource_count = int(arguments.get("resource_count", 0))
            estimate = max(50_000, population * 250 + resource_count * 10_000)
            result = json.dumps({"estimated_cost": str(estimate)})
            trace_event("mcp.scenario.response", tool=name, result=result)
            return result

        if name == "scenario_compare":
            scenarios = list(arguments.get("scenarios", []))
            ranked = sorted(scenarios, key=lambda s: float(s.get("score", 0.0)), reverse=True)
            best = ranked[0] if ranked else None
            result = json.dumps({"best": best, "ranked": ranked})
            trace_event("mcp.scenario.response", tool=name, result=result)
            return result

        raise KeyError(f"Unsupported tool: {name}")
