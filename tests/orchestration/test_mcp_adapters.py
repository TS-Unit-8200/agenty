import json
from unittest.mock import patch

from agenty.mcp_gateway import CommsMockMCPServer, MCPGateway, PhoneCallMCPServer, ScenarioGenMCPServer


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


def test_comms_mock_phone_contract_returns_call_id_and_result() -> None:
    server = CommsMockMCPServer(llm=None)
    started = json.loads(
        server.call_tool(
            "phone_agent_start_call",
            {
                "phone_number": "+48123456789",
                "schema": {"type": "object", "properties": {"availability": {"type": "string"}}},
                "requirements": "Potwierdz dostepnosc.",
                "resource_name": "SP ZOZ Lublin",
            },
        )
    )
    polled = json.loads(server.call_tool("phone_agent_get_call", {"call_id": started["call_id"]}))
    assert started["status"] == "initiated"
    assert polled["status"] == "completed"
    assert "result" in polled


def test_phone_call_adapter_sends_bearer_and_path() -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{\"call_id\":\"call-1\",\"status\":\"initiated\"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["timeout"] = timeout
        return FakeResponse()

    server = PhoneCallMCPServer(base_url="http://phone.local", api_token="secret", timeout_s=12.0)
    with patch("agenty.mcp_gateway.phone_call.urlopen", fake_urlopen):
        payload = json.loads(
            server.call_tool(
                "phone_agent_start_call",
                {"phone_number": "+48123456789", "schema": {"type": "object"}, "requirements": "Ping"},
            )
        )
    assert payload["call_id"] == "call-1"
    assert captured["url"] == "http://phone.local/calls"
    assert captured["auth"] == "Bearer secret"
    assert captured["timeout"] == 12.0


def test_gateway_dispatches_to_matching_server() -> None:
    gateway = MCPGateway([ScenarioGenMCPServer(), CommsMockMCPServer(llm=None)])
    payload = json.loads(gateway.call_tool("scenario_estimate_cost", {"affected_population": 1, "resource_count": 0}))
    assert "estimated_cost" in payload
