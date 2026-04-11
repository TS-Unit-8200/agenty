"""Orchestration engine for incident-driven async workflow execution (LangGraph)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from agenty.agent import AgentRuntime
from agenty.mcp_gateway.base import MCPGateway
from agenty.orchestration.agent_runner import AgentRunner
from agenty.orchestration.agent_selector import AgentSelector
from agenty.orchestration.crisis_graph import build_crisis_graph
from agenty.orchestration.crisis_workflow_nodes import CrisisWorkflowNodes
from agenty.orchestration.hierarchy_service import HierarchyService
from agenty.orchestration.models import OrchestrationResult, WorkflowRun
from agenty.orchestration.reconciliation import ReconciliationService
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.scenario_service import ScenarioService
from agenty.api.access_log import agenty_echo
from agenty.orchestration.tracing import trace_event, trace_human_block
from langgraph.checkpoint.memory import MemorySaver


class OrchestrationEngine:
    def __init__(
        self,
        *,
        repository: OrchestrationRepository,
        runtime: AgentRuntime,
        mcp: MCPGateway,
        orchestrator_version: str = "v1",
        max_step_retries: int = 2,
        checkpointer: MemorySaver | None = None,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._mcp = mcp
        self._orchestrator_version = orchestrator_version
        self._max_step_retries = max_step_retries  # reserved for future node-level retries
        self._hierarchy = HierarchyService(repository)
        self._selector = AgentSelector()
        self._runner = AgentRunner(
            runtime,
            timeout_s=max(30.0, float(runtime._settings.agent_llm_timeout_s)),
        )
        self._reconciliation = ReconciliationService()
        self._scenarios = ScenarioService(mcp)
        self._nodes = CrisisWorkflowNodes(
            repository=repository,
            hierarchy=self._hierarchy,
            selector=self._selector,
            runner=self._runner,
            reconciliation=self._reconciliation,
            scenarios=self._scenarios,
            mcp=mcp,
        )
        self._checkpointer = checkpointer or MemorySaver()
        self._graph = build_crisis_graph(self._nodes, checkpointer=self._checkpointer)

    def start_run(self, *, incident_id: str, org_id: str) -> WorkflowRun:
        existing = self._repository.find_existing_run(incident_id, self._orchestrator_version)
        if existing:
            trace_event("orchestration.run.reused", run_id=existing.id, incident_id=incident_id, org_id=org_id)
            return existing
        now = datetime.now(UTC)
        run = WorkflowRun(
            id=str(uuid4()),
            incident_id=incident_id,
            org_id=org_id,
            orchestrator_version=self._orchestrator_version,
            status="created",
            current_state="created",
            started_at=now,
            updated_at=now,
        )
        trace_event("orchestration.run.created", run_id=run.id, incident_id=incident_id, org_id=org_id)
        return self._repository.create_run(run)

    async def execute(self, run_id: str) -> OrchestrationResult:
        run = self._repository.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown run id {run_id}")
        trace_human_block(
            f"Orchestration started (LangGraph)  │  run {run_id}",
            f"incident_id={run.incident_id}\norg_id={run.org_id}",
        )
        trace_event("orchestration.graph.invoke", run_id=run.id)
        agenty_echo(
            f"[agenty] OrchestrationEngine.execute(run_id={run_id}) — "
            f"running LangGraph (hierarchy → agents → reconcile → scenarios → resources → comms)",
        )

        initial = {
            "run_id": run.id,
            "incident_id": run.incident_id,
            "org_id": run.org_id,
        }
        config = {"configurable": {"thread_id": run.id}}

        try:
            await self._graph.ainvoke(initial, config)
        except Exception as exc:  # noqa: BLE001
            trace_event("orchestration.graph.error", run_id=run.id, error=str(exc))
            trace_human_block(
                f"Orchestration graph aborted  │  run {run.id}",
                str(exc),
            )

        latest_run = self._repository.get_run(run.id)
        if latest_run is None:
            raise RuntimeError("Run disappeared while finalizing")
        agenty_echo(
            f"[agenty] OrchestrationEngine.execute(run_id={run_id}) — finished; "
            f"run.status={latest_run.status!r} current_state={latest_run.current_state!r}",
        )

        steps = self._repository.list_steps(latest_run.id)
        agent_runs = self._repository.list_agent_runs(latest_run.id)
        scenario_id = None
        for step in steps:
            if step.state == "generate_scenarios":
                scenario_id = step.output_payload.get("scenario_version_id")
        scenario_version = self._repository.get_scenario_version(str(scenario_id)) if scenario_id else None
        comms_summary = None
        for step in steps:
            if step.state == "comms_mock_call":
                comms_summary = str(step.output_payload.get("summary", ""))

        return OrchestrationResult(
            run=latest_run,
            steps=steps,
            agent_runs=agent_runs,
            scenario_version=scenario_version,
            comms_summary=comms_summary,
        )
