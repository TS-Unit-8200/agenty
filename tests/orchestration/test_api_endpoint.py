"""Unit tests for the orchestration router."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenty.api.routes_orchestration import create_orchestration_router


class FakeEngine:
    def __init__(self) -> None:
        self.schedule_calls: list[tuple[str, bool]] = []
        self.ensure_calls: list[tuple[str, str]] = []

    def ensure_run(self, *, incident_id: str, org_id: str) -> tuple[SimpleNamespace, bool]:
        self.ensure_calls.append((incident_id, org_id))
        return SimpleNamespace(id="run-123", status="run_orchestrator"), True

    def schedule(self, run_id: str, *, resume: bool = False) -> bool:
        self.schedule_calls.append((run_id, resume))
        return True


class FakeRepository:
    def get_run(self, run_id: str) -> SimpleNamespace | None:
        if run_id != "run-123":
            return None
        return SimpleNamespace(
            id="run-123",
            status="run_orchestrator",
            current_state="run_orchestrator",
            updated_at="2026-04-12T10:10:00Z",
            model_dump=lambda *args, **kwargs: {
                "id": "run-123",
                "status": "run_orchestrator",
                "current_state": "run_orchestrator",
                "updated_at": "2026-04-12T10:10:00Z",
            },
        )

    def list_steps(self, run_id: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                state="generate_scenarios",
                output_payload={"scenario_version_id": "scenario-1"},
                model_dump=lambda *args, **kwargs: {
                    "state": "generate_scenarios",
                    "output_payload": {"scenario_version_id": "scenario-1"},
                },
            )
        ]

    def list_agent_runs(self, run_id: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                agent_id="komendant-policji",
                response="Stara odpowiedz.",
                status="completed",
                summary={
                    "perspective": "Wczesniejsza perspektywa.",
                    "concerns": ["Stare ryzyko."],
                    "recommendations": ["Stare zalecenie."],
                    "urgency": "hours",
                },
                model_dump=lambda *args, **kwargs: {
                    "agent_id": "komendant-policji",
                    "response": "Stara odpowiedz.",
                    "status": "completed",
                    "summary": {
                        "perspective": "Wczesniejsza perspektywa.",
                        "concerns": ["Stare ryzyko."],
                        "recommendations": ["Stare zalecenie."],
                        "urgency": "hours",
                    },
                },
            ),
            SimpleNamespace(
                agent_id="komendant-policji",
                response="Zamknac wezly i ustawic objazd.",
                status="completed",
                summary={
                    "perspective": "Priorytetem jest zamkniecie wezlow.",
                    "concerns": ["Wtorny korek."],
                    "recommendations": ["Wyznaczyc objazd."],
                    "urgency": "hours",
                },
                model_dump=lambda *args, **kwargs: {
                    "agent_id": "komendant-policji",
                    "response": "Zamknac wezly i ustawic objazd.",
                    "status": "completed",
                    "summary": {
                        "perspective": "Priorytetem jest zamkniecie wezlow.",
                        "concerns": ["Wtorny korek."],
                        "recommendations": ["Wyznaczyc objazd."],
                        "urgency": "hours",
                    },
                },
            ),
            SimpleNamespace(
                agent_id="orchestrator",
                response="# Raport",
                status="timed_out",
                summary=None,
                model_dump=lambda *args, **kwargs: {
                    "agent_id": "orchestrator",
                    "response": "# Raport",
                    "status": "timed_out",
                    "summary": None,
                },
            ),
        ]

    def get_scenario_version(self, version_id: str) -> SimpleNamespace | None:
        if version_id != "scenario-1":
            return None
        return SimpleNamespace(
            model_dump=lambda *args, **kwargs: {
                "id": "scenario-1",
                "recommendation_label": "A",
                "rationale": "Najkrotszy czas reakcji.",
                "scenarios": [
                    {
                        "label": "A",
                        "title": "Korytarz awaryjny",
                        "estimated_cost": "75000",
                        "time_to_resolve": "Do 12-24 h",
                        "is_recommended": True,
                        "actions": {"h2": ["Zamknac wezly"]},
                    }
                ],
            }
        )

    def get_external_info_request(self, run_id: str) -> SimpleNamespace | None:
        if run_id != "run-123":
            return None
        return SimpleNamespace(
            model_dump=lambda *args, **kwargs: {
                "status": "waiting",
                "resource_id": "res-hosp-1",
                "resource_name": "SP ZOZ Lublin",
                "call_id": "call-123",
                "notice": "Trwa rozmowa z SP ZOZ Lublin.",
                "result": None,
                "updated_at": "2026-04-12T10:11:00Z",
            }
        )


def make_client() -> tuple[TestClient, FakeEngine]:
    engine = FakeEngine()
    app = FastAPI()
    app.include_router(create_orchestration_router(engine=engine, repository=FakeRepository()))
    return TestClient(app), engine


def test_orchestration_post_is_idempotent_and_reuses_existing_run() -> None:
    client, engine = make_client()

    response = client.post("/orchestrations", json={"incident_id": "inc-123", "org_id": "org-9"})

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-123", "status": "run_orchestrator"}
    assert engine.ensure_calls == [("inc-123", "org-9")]
    assert engine.schedule_calls == [("run-123", True)]


def test_resume_endpoint_is_noop_for_known_nonterminal_run_and_schedules_resume() -> None:
    client, engine = make_client()

    response = client.post("/orchestrations/run-123/resume")

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-123", "status": "run_orchestrator"}
    assert engine.schedule_calls == [("run-123", True)]


def test_result_endpoint_omits_steps_by_default_and_can_opt_in_debug_steps() -> None:
    client, _engine = make_client()

    result = client.get("/orchestrations/run-123/result")

    assert result.status_code == 200
    assert result.json()["scenario_version"]["id"] == "scenario-1"
    assert result.json()["scenario_version"]["scenarios"][0]["label"] == "A"
    assert len(result.json()["agent_runs"]) == 2
    assert result.json()["agent_runs"][0]["response"] == "Zamknac wezly i ustawic objazd."
    assert result.json()["orchestrator_report"] == "# Raport"
    assert result.json()["external_info"]["call_id"] == "call-123"
    assert "steps" not in result.json()

    debug_result = client.get("/orchestrations/run-123/result?include_steps=true")
    assert debug_result.status_code == 200
    assert debug_result.json()["steps"][0]["state"] == "generate_scenarios"
