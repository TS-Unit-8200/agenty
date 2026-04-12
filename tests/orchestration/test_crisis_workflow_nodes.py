import asyncio
import json
from datetime import UTC, datetime

import pytest

from agenty.orchestration.agent_runner import AgentExecutionOutcome
from agenty.orchestration.crisis_workflow_nodes import CrisisWorkflowNodes
from agenty.orchestration.exceptions import WorkflowPause
from agenty.orchestration.models import (
    AgentRun,
    AgentRunSummary,
    AgentToolSession,
    ExternalInfoRequest,
    WorkflowRun,
    WorkflowStep,
)


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
        self.agent_tool_sessions: dict[str, AgentToolSession] = {}
        self.external_request: ExternalInfoRequest | None = None

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

    def upsert_agent_tool_session(self, session: AgentToolSession) -> AgentToolSession:
        self.agent_tool_sessions[session.agent_id] = session
        return session

    def get_agent_tool_session(self, run_id: str, agent_id: str) -> AgentToolSession | None:
        if run_id != self.run.id:
            return None
        return self.agent_tool_sessions.get(agent_id)

    def delete_agent_tool_session(self, run_id: str, agent_id: str) -> None:
        if run_id != self.run.id:
            return
        self.agent_tool_sessions.pop(agent_id, None)

    def get_external_info_request(self, run_id: str):
        if run_id != self.run.id:
            return None
        return self.external_request

    def upsert_external_info_request(self, request: ExternalInfoRequest) -> ExternalInfoRequest:
        self.external_request = request
        return request


class FakeMCP:
    def __init__(self, resources: list[dict[str, object]]) -> None:
        self.resources = resources
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(self, name: str, arguments: dict[str, object]) -> str:
        self.calls.append((name, arguments))
        if name == "resource_list":
            return json.dumps(self.resources, ensure_ascii=False)
        raise KeyError(name)


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
        execution_mode: str | None = None,
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
        execution_mode: str | None = None,
    ) -> list[AgentRun]:
        raise RuntimeError("boom")


class DummyService:
    def __getattr__(self, _name: str) -> object:
        raise AttributeError


class FakePlannerLlm:
    def chat_completion(self, *_args, **_kwargs) -> str:
        return '{"should_call": false}'


class PausingCouncilRunner(FakeRunner):
    async def run_council_agent(
        self,
        *,
        run_id: str,
        incident_id: str,
        agent_id: str,
        prompt: str,
        context_sections: dict[str, str],
        repository,
        mcp,
        timeout_s: float | None = None,
        execution_mode: str | None = None,
    ) -> AgentExecutionOutcome:
        now = datetime.now(UTC)
        request = ExternalInfoRequest(
            id="ext-1",
            run_id=run_id,
            incident_id=incident_id,
            resource_id="res-police",
            resource_name="KPP Swidnik - dyzurny ruchu",
            phone_number="+48695031104",
            resource_type="police",
            contact_name="Dyżurny ruchu",
            contact_role="komendant-policji",
            owner_agent_id=agent_id,
            call_id="call-1",
            preferred_contact_type="police",
            unknowns=["Dokladny kilometr zdarzenia na S17"],
            schema_def={},
            requirements="Potwierdz lokalizacje i przejezdnosc.",
            reason="Brakuje potwierdzenia od dyzurnego.",
            status="waiting",
            budget_status="reserved",
            notice="Trwa rozmowa z KPP Swidnik - dyzurny ruchu.",
            created_at=now,
            updated_at=now,
        )
        repository.upsert_external_info_request(request)
        return AgentExecutionOutcome(
            agent_run=AgentRun(
                run_id=run_id,
                agent_id=agent_id,
                status="waiting_tool",
                started_at=now,
                finished_at=now,
                latency_ms=120,
                tool_status="waiting_tool",
                tool_notice=request.notice,
                tool_resource_id=request.resource_id,
                tool_resource_name=request.resource_name,
            ),
            paused=True,
            session=AgentToolSession(
                run_id=run_id,
                agent_id=agent_id,
                tool_name="phone_query_resource",
                tool_call_id="tool-call-1",
                messages=[{"role": "assistant", "content": "tool call pending"}],
                created_at=now,
                updated_at=now,
            ),
            external_request=request,
        )


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
        planner_llm=FakePlannerLlm(),
        phone_poll_interval_s=5.0,
        phone_max_wait_s=60.0,
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
        planner_llm=FakePlannerLlm(),
        phone_poll_interval_s=5.0,
        phone_max_wait_s=60.0,
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


def test_plan_external_info_forces_phone_call_for_explicit_unknowns() -> None:
    repository = FakeRepository()
    repository.agent_runs = [
        AgentRun(
            run_id="run-1",
            agent_id="dyrektor-abw",
            status="completed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            latency_ms=900,
            response=(
                "## Czego nie wiem - luki analityczne\n"
                "1. Tozsamosc i profil instalacji - czy to zaklad SEVESO?\n"
                "2. Sklad chemiczny emisji - od WIOS/GIS.\n"
            ),
            summary=AgentRunSummary(
                perspective="Potrzebna weryfikacja operatora.",
                concerns=["Nieznane logi SCADA i brak danych operatora."],
                recommendations=["Skontaktowac sie z operatorem instalacji."],
                urgency="immediate",
            ),
        )
    ]
    mcp = FakeMCP(
        [
            {
                "resource_id": "res_hospital",
                "name": "Szpital Powiatowy",
                "type": "hospital",
                "contact_phone": "+48111222333",
                "contact_role": "dyrektor-szpitala",
            },
            {
                "resource_id": "res_operator",
                "name": "Operator zakladu chemicznego",
                "type": "operator",
                "contact_phone": "+48444555666",
                "contact_role": "operator instalacji",
            },
        ]
    )
    nodes = CrisisWorkflowNodes(
        repository=repository,
        hierarchy=DummyService(),
        selector=DummyService(),
        runner=FakeRunner(),
        reconciliation=DummyService(),
        scenarios=DummyService(),
        mcp=mcp,
        planner_llm=FakePlannerLlm(),
        phone_poll_interval_s=5.0,
        phone_max_wait_s=60.0,
    )

    state = {
        "run_id": "run-1",
        "fetch_hierarchy": {
            "incident": {
                "id": "inc-1",
                "title": "Pozar przemyslowy",
                "priority": "critical",
                "description": "Pozar instalacji przemyslowej z emisja.",
            },
        },
        "resolve_conflicts": {
            "agreements": [],
            "conflicts": [],
            "gaps": [],
        },
    }

    result = asyncio.run(nodes.plan_external_info(state))

    assert result["plan_external_info"]["should_call"] is True
    assert result["plan_external_info"]["resource_id"] == "res_operator"
    assert "Tozsamosc i profil instalacji" in "\n".join(result["plan_external_info"]["explicit_gaps"])
    assert repository.external_request is not None
    assert repository.external_request.resource_id == "res_operator"
    assert "SEVESO" in repository.external_request.requirements
    assert mcp.calls[0][0] == "resource_list"


def test_plan_external_info_uses_fallback_phone_when_incident_has_no_resources() -> None:
    repository = FakeRepository()
    repository.agent_runs = [
        AgentRun(
            run_id="run-1",
            agent_id="dyrektor-abw",
            status="completed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            latency_ms=700,
            response=(
                "## Czego nie wiem - luki analityczne\n"
                "- Tozsamosc operatora instalacji i logi SCADA.\n"
            ),
            summary=AgentRunSummary(
                perspective="Brakuje potwierdzenia od operatora.",
                concerns=["Nieznane dane operatora i systemow sterowania."],
                recommendations=["Wykonac telefon w celu potwierdzenia sytuacji."],
                urgency="immediate",
            ),
        )
    ]
    mcp = FakeMCP([])
    nodes = CrisisWorkflowNodes(
        repository=repository,
        hierarchy=DummyService(),
        selector=DummyService(),
        runner=FakeRunner(),
        reconciliation=DummyService(),
        scenarios=DummyService(),
        mcp=mcp,
        planner_llm=FakePlannerLlm(),
        phone_poll_interval_s=5.0,
        phone_max_wait_s=60.0,
        phone_agent_default_phone_number="+48695031104",
    )

    state = {
        "run_id": "run-1",
        "fetch_hierarchy": {
            "incident": {
                "id": "inc-1",
                "title": "Pozar przemyslowy",
                "priority": "critical",
                "description": "Pozar instalacji przemyslowej z emisja.",
                "resources": [],
            },
        },
        "resolve_conflicts": {
            "agreements": [],
            "conflicts": [],
            "gaps": [],
        },
    }

    result = asyncio.run(nodes.plan_external_info(state))

    assert result["plan_external_info"]["should_call"] is True
    assert repository.external_request is not None
    assert repository.external_request.phone_number == "+48695031104"
    assert repository.external_request.resource_id == "fallback_operator_phone"
    assert repository.external_request.owner_agent_id == "dyrektor-abw"


def test_run_agents_async_pause_does_not_fail_run() -> None:
    repository = FakeRepository()
    repository.agent_runs = []
    nodes = CrisisWorkflowNodes(
        repository=repository,
        hierarchy=DummyService(),
        selector=DummyService(),
        runner=PausingCouncilRunner(),
        reconciliation=DummyService(),
        scenarios=DummyService(),
        mcp=DummyService(),
        planner_llm=FakePlannerLlm(),
        phone_poll_interval_s=5.0,
        phone_max_wait_s=60.0,
    )

    state = {
        "run_id": "run-1",
        "fetch_hierarchy": {
            "incident": {"id": "inc-1", "description": "Karambol na S17."},
            "organization": {"name": "Lubelskie"},
        },
        "select_agents": {
            "agents": ["komendant-policji"],
        },
    }

    with pytest.raises(WorkflowPause):
        asyncio.run(nodes.run_agents_async(state))

    assert repository.run.status == "run_agents_async"
    assert repository.run.current_state == "run_agents_async"
    assert repository.run.completed_at is None

    step = repository.steps["run_agents_async"]
    assert step.status == "running"
    assert step.error is None
    assert step.finished_at is None
    assert step.output_payload["waiting_agent"] == "komendant-policji"

    assert repository.agent_runs[-1].status == "waiting_tool"
    assert repository.agent_runs[-1].tool_status == "waiting_tool"
    assert repository.external_request is not None
    assert repository.external_request.status == "waiting"
    assert repository.get_agent_tool_session("run-1", "komendant-policji") is not None
