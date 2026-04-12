"""Orchestration engine for incident-driven async workflow execution and recovery."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver

from agenty.agent import AgentRuntime
from agenty.api.access_log import agenty_echo
from agenty.mcp_gateway.base import MCPGateway
from agenty.orchestration.agent_runner import AgentRunner
from agenty.orchestration.agent_selector import AgentSelector
from agenty.orchestration.crisis_graph import build_crisis_graph
from agenty.orchestration.crisis_graph_state import CrisisGraphState
from agenty.orchestration.crisis_workflow_nodes import CrisisWorkflowNodes
from agenty.orchestration.exceptions import WorkflowPause
from agenty.orchestration.hierarchy_service import HierarchyService
from agenty.orchestration.models import OrchestrationResult, WorkflowRun, WorkflowState
from agenty.orchestration.reconciliation import ReconciliationService
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.scenario_service import ScenarioService
from agenty.orchestration.state_machine import TERMINAL_STATES
from agenty.orchestration.tracing import trace_event, trace_human_block

logger = logging.getLogger(__name__)

WORKFLOW_STEPS: tuple[WorkflowState, ...] = (
    "fetch_hierarchy",
    "select_agents",
    "run_agents_async",
    "resolve_conflicts",
    "run_orchestrator",
    "generate_scenarios",
    "sync_resources",
)


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
        self._max_step_retries = max_step_retries
        self._hierarchy = HierarchyService(repository)
        self._selector = AgentSelector()
        self._runner = AgentRunner(
            runtime,
            timeout_s=max(30.0, float(runtime._settings.agent_llm_timeout_s)),
            phone_poll_interval_s=max(1.0, float(runtime._settings.phone_agent_poll_interval_s)),
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
            planner_llm=runtime.llm,
            phone_poll_interval_s=max(1.0, float(runtime._settings.phone_agent_poll_interval_s)),
            phone_max_wait_s=max(1.0, float(runtime._settings.phone_agent_max_wait_s)),
            phone_agent_default_phone_number=runtime._settings.phone_agent_default_phone_number,
        )
        self._checkpointer = checkpointer or MemorySaver()
        self._graph = build_crisis_graph(self._nodes, checkpointer=self._checkpointer)
        self._active_tasks: dict[str, asyncio.Task[OrchestrationResult]] = {}
        self._resume_watchers: dict[str, asyncio.Task[None]] = {}
        self._phone_poll_interval_s = max(1.0, float(runtime._settings.phone_agent_poll_interval_s))

    @property
    def orchestrator_version(self) -> str:
        return self._orchestrator_version

    def start_run(
        self,
        *,
        incident_id: str,
        org_id: str,
        execution_mode: str = "default",
    ) -> WorkflowRun:
        now = datetime.now(UTC)
        run = WorkflowRun(
            id=str(uuid4()),
            incident_id=incident_id,
            org_id=org_id,
            orchestrator_version=self._orchestrator_version,
            execution_mode=execution_mode,
            status="created",
            current_state="created",
            started_at=now,
            updated_at=now,
        )
        trace_event(
            "orchestration.run.created",
            run_id=run.id,
            incident_id=incident_id,
            org_id=org_id,
            execution_mode=execution_mode,
        )
        created = self._repository.create_run(run)
        self._repository.update_incident_links(
            incident_id,
            run_id=created.id,
            scenario_version_id=None,
        )
        return created

    def ensure_run(
        self,
        *,
        incident_id: str,
        org_id: str,
        execution_mode: str = "default",
    ) -> tuple[WorkflowRun, bool]:
        existing = self._repository.find_latest_active_run(
            incident_id,
            self._orchestrator_version,
            execution_mode=execution_mode,
        )
        if existing is not None:
            self._repository.update_incident_links(
                incident_id,
                run_id=existing.id,
                scenario_version_id=self._scenario_version_id(existing.id),
            )
            return existing, True
        return self.start_run(incident_id=incident_id, org_id=org_id, execution_mode=execution_mode), False

    def is_task_active(self, run_id: str) -> bool:
        task = self._active_tasks.get(run_id)
        return bool(task and not task.done())

    def schedule(self, run_id: str, *, resume: bool = False) -> bool:
        current = self._active_tasks.get(run_id)
        if current is not None and not current.done():
            return False
        watcher = self._resume_watchers.pop(run_id, None)
        if watcher is not None and not watcher.done():
            watcher.cancel()

        coroutine = self.resume(run_id) if resume else self.execute(run_id)
        task = asyncio.create_task(coroutine)
        self._active_tasks[run_id] = task
        task.add_done_callback(self._task_done_callback(run_id))
        return True

    def schedule_resume(self, run_id: str, *, delay_s: float | None = None) -> bool:
        current = self._resume_watchers.get(run_id)
        if current is not None and not current.done():
            return False

        effective_delay = max(1.0, delay_s or self._phone_poll_interval_s)

        async def _resume_later() -> None:
            try:
                await asyncio.sleep(effective_delay)
            except asyncio.CancelledError:
                return
            run = self._repository.get_run(run_id)
            if run is None or run.status in TERMINAL_STATES:
                return
            self.schedule(run_id, resume=True)

        watcher = asyncio.create_task(_resume_later())
        self._resume_watchers[run_id] = watcher
        watcher.add_done_callback(self._watcher_done_callback(run_id))
        return True

    def restore_waiting_runs(self) -> int:
        count = 0
        for request in self._repository.list_external_info_requests(statuses=["initiated", "waiting"]):
            run = self._repository.get_run(request.run_id)
            if run is None or run.status in TERMINAL_STATES:
                continue
            if self.schedule_resume(request.run_id, delay_s=self._phone_poll_interval_s):
                count += 1
        return count

    def _task_done_callback(self, run_id: str):
        def _cb(task: asyncio.Task[OrchestrationResult]) -> None:
            self._active_tasks.pop(run_id, None)
            try:
                _ = task.result()
                agenty_echo(
                    f"[agenty] background orchestration task(run_id={run_id}) - finished OK "
                    "(orchestration steps persisted in Mongo)",
                )
            except asyncio.CancelledError:
                agenty_echo(f"[agenty] background orchestration task(run_id={run_id}) - cancelled")
            except Exception as exc:  # noqa: BLE001
                agenty_echo(f"[agenty] background orchestration task(run_id={run_id}) - FAILED: {exc!r}")
                logger.exception("Background orchestration task failed run_id=%s", run_id)

        return _cb

    def _watcher_done_callback(self, run_id: str):
        def _cb(task: asyncio.Task[None]) -> None:
            self._resume_watchers.pop(run_id, None)
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.exception("Resume watcher failed run_id=%s", run_id)
                agenty_echo(f"[agenty] resume watcher(run_id={run_id}) - FAILED: {exc!r}")

        return _cb

    async def execute(self, run_id: str) -> OrchestrationResult:
        run = self._repository.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown run id {run_id}")

        trace_human_block(
            f"Orchestration started (LangGraph)  |  run {run_id}",
            f"incident_id={run.incident_id}\norg_id={run.org_id}",
        )
        trace_event("orchestration.graph.invoke", run_id=run.id)
        agenty_echo(
            f"[agenty] OrchestrationEngine.execute(run_id={run_id}) - "
            "running LangGraph (hierarchy -> council -> reconcile -> external-info -> orchestrator -> scenarios -> resources)",
        )

        initial: CrisisGraphState = {
            "run_id": run.id,
            "incident_id": run.incident_id,
            "org_id": run.org_id,
            "execution_mode": run.execution_mode,
        }
        config = {"configurable": {"thread_id": run.id}}
        paused = False

        try:
            await self._graph.ainvoke(initial, config)
        except WorkflowPause as pause:
            paused = True
            trace_event("orchestration.graph.paused", run_id=run.id, reason=pause.reason, delay_s=pause.delay_s)
            self.schedule_resume(run.id, delay_s=pause.delay_s)
        except asyncio.CancelledError:
            self._mark_cancelled(run.id)
            raise
        except Exception as exc:  # noqa: BLE001
            trace_event("orchestration.graph.error", run_id=run.id, error=str(exc))
            trace_human_block(
                f"Orchestration graph aborted  |  run {run.id}",
                str(exc),
            )

        if not paused:
            self._finalize_completed_run_if_needed(run.id)
        result = self._collect_result(run.id)
        agenty_echo(
            f"[agenty] OrchestrationEngine.execute(run_id={run_id}) - finished; "
            f"run.status={result.run.status!r} current_state={result.run.current_state!r}",
        )
        return result

    async def resume(self, run_id: str) -> OrchestrationResult:
        run = self._repository.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown run id {run_id}")
        if run.status in TERMINAL_STATES:
            return self._collect_result(run.id)

        trace_human_block(
            f"Orchestration resume  |  run {run_id}",
            f"incident_id={run.incident_id}\norg_id={run.org_id}\ncurrent_state={run.current_state}",
        )
        trace_event("orchestration.resume.invoke", run_id=run.id, current_state=run.current_state)
        agenty_echo(
            f"[agenty] OrchestrationEngine.resume(run_id={run_id}) - "
            "rebuilding state from Mongo and continuing missing steps",
        )

        state, start_index = self._build_resume_state(run)
        if start_index >= len(WORKFLOW_STEPS):
            self._finalize_completed_run_if_needed(run.id)
            return self._collect_result(run.id)

        paused = False
        try:
            for step_name in WORKFLOW_STEPS[start_index:]:
                node = getattr(self._nodes, step_name)
                result = await node(state)
                state.update(result)
                latest_run = self._repository.get_run(run.id)
                if latest_run is None or latest_run.status in TERMINAL_STATES:
                    break
        except WorkflowPause as pause:
            paused = True
            trace_event("orchestration.resume.paused", run_id=run.id, reason=pause.reason, delay_s=pause.delay_s)
            self.schedule_resume(run.id, delay_s=pause.delay_s)
        except asyncio.CancelledError:
            self._mark_cancelled(run.id)
            raise
        except Exception as exc:  # noqa: BLE001
            trace_event("orchestration.resume.error", run_id=run.id, error=str(exc))
            trace_human_block(
                f"Orchestration resume aborted  |  run {run.id}",
                str(exc),
            )

        if not paused:
            self._finalize_completed_run_if_needed(run.id)
        result = self._collect_result(run.id)
        agenty_echo(
            f"[agenty] OrchestrationEngine.resume(run_id={run_id}) - finished; "
            f"run.status={result.run.status!r} current_state={result.run.current_state!r}",
        )
        return result

    def _build_resume_state(self, run: WorkflowRun) -> tuple[CrisisGraphState, int]:
        state: CrisisGraphState = {
            "run_id": run.id,
            "incident_id": run.incident_id,
            "org_id": run.org_id,
            "execution_mode": run.execution_mode,
        }
        steps = {step.state: step for step in self._repository.list_steps(run.id)}

        next_index = 0
        for index, step_name in enumerate(WORKFLOW_STEPS):
            step = steps.get(step_name)
            if step is not None and step.status == "completed":
                state[step_name] = step.output_payload
                next_index = index + 1
                continue
            next_index = index
            break
        else:
            next_index = len(WORKFLOW_STEPS)

        return state, next_index

    def _mark_cancelled(self, run_id: str) -> None:
        latest = self._repository.get_run(run_id)
        if latest is None or latest.status in TERMINAL_STATES:
            return
        self._repository.update_run_state(
            run_id,
            status=latest.status,
            current_state=latest.current_state,
            last_error="cancelled",
            completed=False,
        )

    def _finalize_completed_run_if_needed(self, run_id: str) -> None:
        latest = self._repository.get_run(run_id)
        if latest is None or latest.status in TERMINAL_STATES:
            return

        steps = self._repository.list_steps(run_id)
        completed_states = {step.state for step in steps if step.status == "completed"}
        if not set(WORKFLOW_STEPS).issubset(completed_states):
            return

        orchestrator_runs = [
            item for item in self._repository.list_agent_runs(run_id) if item.agent_id == "orchestrator"
        ]
        final_status: WorkflowState = "completed"
        if orchestrator_runs:
            latest_orchestrator = orchestrator_runs[-1]
            if latest_orchestrator.status != "completed":
                final_status = "partial_completed"
        external_info = self._repository.get_external_info_request(run_id)
        if external_info and external_info.status in {"failed", "timed_out"}:
            final_status = "partial_completed"

        self._repository.update_run_state(
            run_id,
            status=final_status,
            current_state=final_status,
            last_error=latest.last_error,
            completed=True,
        )

    def _scenario_version_id(self, run_id: str) -> str | None:
        for step in self._repository.list_steps(run_id):
            if step.state == "generate_scenarios":
                value = step.output_payload.get("scenario_version_id")
                return str(value) if value else None
        return None

    def _collect_result(self, run_id: str) -> OrchestrationResult:
        latest_run = self._repository.get_run(run_id)
        if latest_run is None:
            raise RuntimeError("Run disappeared while finalizing")

        steps = self._repository.list_steps(latest_run.id)
        agent_runs = self._repository.list_agent_runs(latest_run.id)
        scenario_id = self._scenario_version_id(latest_run.id)
        scenario_version = self._repository.get_scenario_version(scenario_id) if scenario_id else None
        external_info = self._repository.get_external_info_request(latest_run.id)
        comms_summary = None
        for step in steps:
            if step.state == "comms_mock_call":
                comms_summary = str(step.output_payload.get("summary", ""))

        orchestrator_report = None
        for agent_run in reversed(agent_runs):
            if agent_run.agent_id == "orchestrator" and agent_run.response:
                orchestrator_report = agent_run.response
                break

        return OrchestrationResult(
            run=latest_run,
            steps=steps,
            agent_runs=agent_runs,
            scenario_version=scenario_version,
            comms_summary=comms_summary,
            orchestrator_report=orchestrator_report,
            external_info=external_info,
        )
