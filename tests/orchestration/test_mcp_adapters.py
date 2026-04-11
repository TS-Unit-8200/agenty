import json

from agenty.mcp_gateway import CommsMockMCPServer, MCPGateway, ScenarioGenMCPServer


def test_scenario_gen_returns_cost_and_score() -> None:
    server = ScenarioGenMCPServer()
    score = json.loads(server.call_tool("scenario_risk_score", {"risks": ["r1", "r2"], "priority": "high"}))
    cost = json.loads(server.call_tool("scenario_estimate_cost", {"affected_population": 100, "resource_count": 2}))
    assert score["score"] > 0
    assert int(cost["estimated_cost"]) >= 50_000


def test_comms_mock_generates_summary_without_llm() -> None:
    server = CommsMockMCPServer(llm=None)
    payload = json.loads(
        server.call_tool(
            "call_user_for_incident_info",
            {"incident_id": "inc-1", "summary": "Blackout in district", "phone": "+48123456789"},
        )
    )
    assert payload["incident_id"] == "inc-1"
    assert "summary" in payload


def test_gateway_dispatches_to_matching_server() -> None:
    gateway = MCPGateway([ScenarioGenMCPServer(), CommsMockMCPServer(llm=None)])
    payload = json.loads(gateway.call_tool("scenario_estimate_cost", {"affected_population": 1, "resource_count": 0}))
    assert "estimated_cost" in payload
