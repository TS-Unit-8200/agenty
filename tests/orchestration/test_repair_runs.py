from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agenty.orchestration.models import WorkflowRun, WorkflowStep
from agenty.orchestration.repair_runs import repair_duplicate_runs


class FakeRepository:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.runs = {
            "run-old": WorkflowRun(
                id="run-old",
                incident_id="inc-1",
                org_id="org-1",
                orchestrator_version="v1",
                status="run_orchestrator",
                current_state="run_orchestrator",
                started_at=now - timedelta(minutes=10),
                updated_at=now - timedelta(minutes=8),
            ),
            "run-new": WorkflowRun(
                id="run-new",
                incident_id="inc-1",
                org_id="org-1",
                orchestrator_version="v1",
                status="run_orchestrator",
                current_state="run_orchestrator",
                started_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=1),
            ),
        }
        self.step_map = {
            "run-old": [
                WorkflowStep(
                    run_id="run-old",
                    state="fetch_hierarchy",
                    status="completed",
                    started_at=now,
                    updated_at=now,
                ),
                WorkflowStep(
                    run_id="run-old",
                    state="select_agents",
                    status="completed",
                    started_at=now,
                    updated_at=now,
                ),
            ],
            "run-new": [
                WorkflowStep(
                    run_id="run-new",
                    state="fetch_hierarchy",
                    status="completed",
                    started_at=now,
                    updated_at=now,
                ),
                WorkflowStep(
                    run_id="run-new",
                    state="select_agents",
                    status="completed",
                    started_at=now,
                    updated_at=now,
                ),
                WorkflowStep(
                    run_id="run-new",
                    state="run_orchestrator",
                    status="completed",
                    started_at=now,
                    updated_at=now,
                    output_payload={"has_report": True},
                ),
            ],
        }
        self.agent_runs = {
            "run-old": [],
            "run-new": [
                type("AgentRunStub", (), {"agent_id": "orchestrator", "response": "# Raport"})(),
            ],
        }
        self.links: list[tuple[str, str, str | None]] = []
        self.superseded: list[tuple[str, str]] = []

    def list_runs(self, *, incident_id: str | None = None, orchestrator_version: str | None = None):
        runs = list(self.runs.values())
        if incident_id is not None:
            runs = [run for run in runs if run.incident_id == incident_id]
        if orchestrator_version is not None:
            runs = [run for run in runs if run.orchestrator_version == orchestrator_version]
        return runs

    def list_steps(self, run_id: str):
        return self.step_map[run_id]

    def list_agent_runs(self, run_id: str):
        return self.agent_runs[run_id]

    def update_incident_links(self, incident_id: str, *, run_id: str, scenario_version_id: str | None):
        self.links.append((incident_id, run_id, scenario_version_id))

    def mark_run_superseded(self, run_id: str, *, superseded_by: str):
        self.superseded.append((run_id, superseded_by))


def test_repair_duplicate_runs_keeps_best_run_and_marks_older_active_duplicates() -> None:
    repository = FakeRepository()

    decisions = repair_duplicate_runs(repository)

    assert decisions[0].canonical_run_id == "run-new"
    assert decisions[0].needs_resume is True
    assert repository.links == [("inc-1", "run-new", None)]
    assert repository.superseded == [("run-old", "run-new")]
