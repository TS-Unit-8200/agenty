"""Unit tests for the orchestration *router* only.

``create_orchestration_router`` is wired with in-memory stand-ins so we can
assert JSON shapes and status codes without Mongo, the LLM, or Next.js. For
tests against the real application factory (same wiring as ``uvicorn``), see
``test_api_server_integration.py`` and run ``pytest -m integration``.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenty.api.routes_orchestration import create_orchestration_router


class FakeEngine:
    def start_run(self, *, incident_id: str, org_id: str) -> SimpleNamespace:
        return SimpleNamespace(id="run-123", status="created")

    async def execute(self, run_id: str) -> None:
        return None


class FakeRepository:
    def get_run(self, run_id: str) -> SimpleNamespace | None:
        if run_id != "run-123":
            return None
        return SimpleNamespace(id="run-123", status="completed", model_dump=lambda: {"id": "run-123", "status": "completed"})

    def list_steps(self, run_id: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                state="generate_scenarios",
                output_payload={"scenario_version_id": "scenario-1"},
                model_dump=lambda: {"state": "generate_scenarios", "output_payload": {"scenario_version_id": "scenario-1"}},
            )
        ]

    def get_scenario_version(self, version_id: str) -> SimpleNamespace | None:
        if version_id != "scenario-1":
            return None
        return SimpleNamespace(model_dump=lambda: {"id": "scenario-1", "recommendation_label": "A"})


def test_orchestration_post_and_result_endpoint_prints_result(capsys) -> None:
    app = FastAPI()
    app.include_router(create_orchestration_router(engine=FakeEngine(), repository=FakeRepository()))

    with TestClient(app) as client:
        response = client.post("/orchestrations", json={"incident_id": "inc-123", "org_id": "org-9"})
        print(response.json())
        assert response.status_code == 200
        assert response.json()["run_id"] == "run-123"

        result = client.get("/orchestrations/run-123/result")
        print(result.json())
        assert result.status_code == 200
        assert result.json()["scenario_version"]["id"] == "scenario-1"

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "run-123" in combined
    assert "scenario-1" in combined
