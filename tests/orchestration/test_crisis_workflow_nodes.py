import asyncio
from datetime import UTC, datetime

from agenty.orchestration.crisis_workflow_nodes import CrisisWorkflowNodes
from agenty.orchestration.models import AgentRun, AgentRunSummary, WorkflowRun, WorkflowStep


class FakeRepository:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.run = WorkflowRun(
            id="run-1",
            incident_id="inc-1",
            org_id="org-1",
            orchestrator_version="v1",
            status="resolve_conflicts",
            current_state="resolve_conflicts",
            started_at=now,
            updated_at=now,
        )
        self.steps: dict[str, WorkflowStep] = {}
        self.agent_runs = [
            AgentRun(
                run_id="run-1",
                agent_id="komendant-policji",
                status="completed",
                started_at=now,
                finished_at=now,
                latency_ms=1200,
                response="RAW-ONLY-TOKEN " * 200,
                summary=AgentRunSummary(
                    perspective="Priorytetem jest zamkniecie wezlow.",
                    concerns=["Wtorny korek."],
                    recommendations=["Wyznaczyc objazd."],
                    urgency="immediate",
                ),
            )
        ]

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self.run if run_id == self.run.id else None

    def update_run_state(self, run_id: str, **kwargs: object) -> None:
        if run_id != self.run.id:
            return
        for key, value in kwargs.items():
            if key == "completed":
                if value:
                    self.run.completed_at = datetime.now(UTC)
                continue
            setattr(self.run, key, value)

    def upsert_step(self, step: WorkflowStep) -> WorkflowStep:
        self.steps[step.state] = step
        return step

    def get_step(self, run_id: str, state: str) -> WorkflowStep | None:
        if run_id != self.run.id:
            return None
        return self.steps.get(state)

    def touch_step(self, run_id: str, state: str) -> None:
        if run_id != self.run.id or state not in self.steps:
            return
        self.steps[state].updated_at = datetime.now(UTC)

    def touch_run(self, run_id: str, *, current_state: str | None = None) -> None:
        if run_id != self.run.id:
            return
        self.run.updated_at = datetime.now(UTC)
        if current_state is not None:
            self.run.current_state = current_state

    def list_agent_runs(self, run_id: str) -> list[AgentRun]:
        return [item for item in self.agent_runs if item.run_id == run_id]

    def append_agent_run(self, agent_run: AgentRun) -> AgentRun:
        self.agent_runs.append(agent_run)
        return agent_run


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, dict[str, str], float | None]] = []

    @property
    def default_timeout_s(self) -> float:
        return 45.0

    async def run(
        self,
        agent_ids: list[str],
        prompt: str,
        context_sections: dict[str, str],
        *,
        timeout_s: float | None = None,
    ) -> list[AgentRun]:
        self.calls.append((agent_ids, prompt, context_sections, timeout_s))
        now = datetime.now(UTC)
        return [
            AgentRun(
                run_id="",
                agent_id="orchestrator",
                status="completed",
                started_at=now,
                finished_at=now,
                latency_ms=2400,
                response="# Incydent: test\n\n## Rekomendowany wariant\n**Plan A**",
            )
        ]


class ExplodingRunner(FakeRunner):
    async def run(
        self,
        agent_ids: list[str],
        prompt: str,
        context_sections: dict[str, str],
        *,
        timeout_s: float | None = None,
    ) -> list[AgentRun]:
        raise RuntimeError("boom")


class DummyService:
    def __getattr__(self, _name: str) -> object:
        raise AttributeError


def test_run_orchestrator_happens_after_reconciliation() -> None:
    repository = FakeRepository()
    runner = FakeRunner()
    nodes = CrisisWorkflowNodes(
        repository=repository,
        hierarchy=DummyService(),
        selector=DummyService(),
        runner=runner,
        reconciliation=DummyService(),
        scenarios=DummyService(),
        mcp=DummyService(),
    )

    state = {
        "run_id": "run-1",
        "fetch_hierarchy": {
            "incident": {"id": "inc-1", "description": "Karambol."},
            "organization": {"name": "Lubelskie"},
        },
        "resolve_conflicts": {
            "agreements": ["PSP + Policja: zamknac wezly"],
            "conflicts": [],
            "gaps": [],
        },
    }

    result = asyncio.run(nodes.run_orchestrator(state))

    assert result["run_orchestrator"]["status"] == "completed"
    assert runner.calls[0][0] == ["orchestrator"]
    assert "Zgodnosci i konflikty" in runner.calls[0][2]
    assert "Priorytetem jest zamkniecie wezlow." in runner.calls[0][2]["Rada - odpowiedzi zrodlowe"]
    assert "RAW-ONLY-TOKEN" not in runner.calls[0][2]["Rada - odpowiedzi zrodlowe"]
    assert repository.agent_runs[-1].agent_id == "orchestrator"
    assert repository.agent_runs[-1].summary is not None


def test_run_orchestrator_failure_does_not_abort_scenario_pipeline() -> None:
    repository = FakeRepository()
    runner = ExplodingRunner()
    nodes = CrisisWorkflowNodes(
        repository=repository,
        hierarchy=DummyService(),
        selector=DummyService(),
        runner=runner,
        reconciliation=DummyService(),
        scenarios=DummyService(),
        mcp=DummyService(),
    )

    state = {
        "run_id": "run-1",
        "fetch_hierarchy": {
            "incident": {"id": "inc-1", "description": "Karambol."},
            "organization": {"name": "Lubelskie"},
        },
        "resolve_conflicts": {
            "agreements": ["PSP + Policja: zamknac wezly"],
            "conflicts": [],
            "gaps": [],
        },
    }

    result = asyncio.run(nodes.run_orchestrator(state))

    assert result["run_orchestrator"]["status"] == "failed"
    assert repository.run.status == "run_orchestrator"
    assert repository.run.current_state == "run_orchestrator"
    assert repository.agent_runs[-1].agent_id == "orchestrator"
    assert repository.agent_runs[-1].status == "failed"
