"""LangGraph node bodies: Mongo step tracing + council agent batch (shared context)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agenty.orchestration.agent_runner import AgentRunner
from agenty.orchestration.agent_selector import AgentSelector
from agenty.orchestration.crisis_graph_state import CrisisGraphState
from agenty.orchestration.hierarchy_service import HierarchyService
from agenty.orchestration.models import WorkflowRun, WorkflowState, WorkflowStep
from agenty.orchestration.reconciliation import ReconciliationService
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.scenario_service import ScenarioService
from agenty.orchestration.tracing import trace_event, trace_human_block

if TYPE_CHECKING:
    from agenty.mcp_gateway.base import MCPGateway

logger = logging.getLogger(__name__)

# Shared council context so each role knows others answer in parallel (then reconciliation).
_COUNCIL_INSTRUCTION = (
    "Jesteś członkiem rady agentów CrisisTwin. Inne role (wymienione w sekcji «Rada») "
    "otrzymują ten sam opis incydentu i odpowiadają równolegle, niezależnie od Ciebie. "
    "Po zebraniu głosów orchestrator zestawi perspektywy, zgodności i konflikty — "
    "nie zakładaj, że widzisz ich pełne odpowiedzi; opieraj się na danych w kontekście."
)


def _step_input_payload(state: CrisisGraphState, step: WorkflowState) -> dict[str, Any]:
    if step == "fetch_hierarchy":
        return {}
    order: list[WorkflowState] = [
        "fetch_hierarchy",
        "select_agents",
        "run_agents_async",
        "resolve_conflicts",
        "generate_scenarios",
        "sync_resources",
        "comms_mock_call",
    ]
    idx = order.index(step)
    if idx == 0:
        return {}
    prev_key = order[idx - 1]
    previous = state.get(prev_key)
    return previous if isinstance(previous, dict) else {}


class CrisisWorkflowNodes:
    """One async node per workflow state; updates ``WorkflowStep`` rows like the legacy engine."""

    def __init__(
        self,
        *,
        repository: OrchestrationRepository,
        hierarchy: HierarchyService,
        selector: AgentSelector,
        runner: AgentRunner,
        reconciliation: ReconciliationService,
        scenarios: ScenarioService,
        mcp: MCPGateway,
    ) -> None:
        self._repository = repository
        self._hierarchy = hierarchy
        self._selector = selector
        self._runner = runner
        self._reconciliation = reconciliation
        self._scenarios = scenarios
        self._mcp = mcp

    def _run(self, state: CrisisGraphState) -> WorkflowRun:
        run = self._repository.get_run(state["run_id"])
        if run is None:
            raise KeyError(f"Unknown run id {state['run_id']}")
        return run

    def _begin_step(self, run: WorkflowRun, name: WorkflowState, state: CrisisGraphState) -> WorkflowStep:
        trace_event("orchestration.step.start", run_id=run.id, state=name)
        self._repository.update_run_state(run.id, status=name, current_state=name)
        step = WorkflowStep(
            run_id=run.id,
            state=name,
            status="running",
            started_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input_payload=_step_input_payload(state, name),
        )
        self._repository.upsert_step(step)
        return step

    def _complete_step(
        self,
        run: WorkflowRun,
        step: WorkflowStep,
        name: WorkflowState,
        output: dict[str, Any],
        *,
        mark_run_completed: bool,
    ) -> None:
        step.status = "completed"
        step.output_payload = output
        step.updated_at = datetime.now(UTC)
        step.finished_at = datetime.now(UTC)
        self._repository.upsert_step(step)
        trace_event("orchestration.step.complete", run_id=run.id, state=name, output_keys=list(output.keys()))
        try:
            preview = json.dumps(output, indent=2, ensure_ascii=False, default=str)
        except TypeError:
            preview = str(output)
        if len(preview) > 14_000:
            preview = preview[:14_000] + "\n… [truncated]"
        trace_human_block(
            f"Workflow step «{name}» complete  │  run {run.id}",
            preview,
        )
        if mark_run_completed:
            self._repository.update_run_state(
                run.id,
                status="completed",
                current_state="completed",
                completed=True,
            )
        else:
            self._repository.update_run_state(
                run.id,
                status=name,
                current_state=name,
            )

    def _fail_step(self, run: WorkflowRun, step: WorkflowStep, name: WorkflowState, exc: BaseException) -> None:
        step.status = "failed"
        step.error = str(exc)
        step.updated_at = datetime.now(UTC)
        step.finished_at = datetime.now(UTC)
        self._repository.upsert_step(step)
        self._repository.update_run_state(
            run.id,
            status="failed",
            current_state="failed",
            last_error=str(exc),
            completed=True,
        )
        trace_human_block(
            f"Workflow step «{name}» FAILED  │  run {run.id}",
            str(exc),
        )

    async def fetch_hierarchy(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "fetch_hierarchy"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            context = self._hierarchy.load_context(run.incident_id)
            trace_event("orchestration.hierarchy.loaded", run_id=run.id, incident_id=run.incident_id)
            output: dict[str, Any] = {
                "organization": context["organization"],
                "incident": context["incident"],
                "hierarchy_found": bool(context["hierarchy"]),
                "hierarchy": context["hierarchy"],
            }
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def select_agents(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "select_agents"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            selected = self._selector.select(
                hierarchy=dict(fetched.get("hierarchy", {})),
                incident=dict(fetched.get("incident", {})),
            )
            trace_event("orchestration.agents.selected", run_id=run.id, agents=selected)
            output = {"agents": selected}
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def run_agents_async(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "run_agents_async"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            selected = state.get("select_agents")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            if not isinstance(selected, dict):
                raise TypeError("select_agents payload must be a dict")
            agent_ids = list(selected.get("agents", []))
            incident = dict(fetched.get("incident", {}))
            roster = ", ".join(agent_ids) if agent_ids else "(brak)"
            context_sections: dict[str, str] = {
                "Incident": json.dumps(incident, ensure_ascii=True),
                "Organization": json.dumps(fetched.get("organization", {}), ensure_ascii=True),
                "Rada": (
                    f"Role w tej turze (odpowiedzi równoległe): {roster}.\n"
                    f"{_COUNCIL_INSTRUCTION}"
                ),
            }
            prompt = (
                "Analyze this incident from your role perspective. "
                "Provide key actions, risks, and immediate priorities. "
                "Remember other specialists answer in parallel with the same incident data; "
                "your perspective will later be reconciled with theirs."
            )
            runs = await self._runner.run(agent_ids, prompt, context_sections)
            for item in runs:
                item.run_id = run.id
                self._repository.append_agent_run(item)
            trace_event(
                "orchestration.agents.ran",
                run_id=run.id,
                agents=[item.agent_id for item in runs],
                failed=[item.agent_id for item in runs if item.status != "completed"],
            )
            failures = [item for item in runs if item.status != "completed"]
            output = {
                "total": len(runs),
                "failures": len(failures),
                "failed_agents": [item.agent_id for item in failures],
            }
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def resolve_conflicts(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "resolve_conflicts"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            agent_runs = self._repository.list_agent_runs(run.id)
            recon = self._reconciliation.reconcile(agent_runs)
            trace_event(
                "orchestration.conflicts.resolved",
                run_id=run.id,
                agreements=len(recon.get("agreements", [])),
                conflicts=len(recon.get("conflicts", [])),
            )
            self._complete_step(run, step, name, recon, mark_run_completed=False)
            return {name: recon}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def generate_scenarios(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "generate_scenarios"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            recon = state.get("resolve_conflicts")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            if not isinstance(recon, dict):
                raise TypeError("resolve_conflicts payload must be a dict")
            incident = dict(fetched.get("incident", {}))
            resource_count = len(incident.get("resources", []))
            scenario_version = self._scenarios.build(
                run_id=run.id,
                incident_id=run.incident_id,
                priority=str(incident.get("priority", "medium")),
                affected_population=int(incident.get("affected_population", 0)),
                resource_count=resource_count,
                reconciliation=recon,
            )
            self._repository.save_scenario_version(scenario_version)
            self._repository.update_incident_links(
                run.incident_id,
                run_id=run.id,
                scenario_version_id=scenario_version.id,
            )
            trace_event(
                "orchestration.scenarios.generated",
                run_id=run.id,
                scenario_version_id=scenario_version.id,
                recommendation=scenario_version.recommendation_label,
            )
            output = {
                "scenario_version_id": scenario_version.id,
                "recommended": scenario_version.recommendation_label,
            }
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def sync_resources(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "sync_resources"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            payload = json.loads(self._mcp.call_tool("resource_list", {"incident_id": run.incident_id}))
            trace_event(
                "orchestration.resources.synced",
                run_id=run.id,
                resource_count=len(payload) if hasattr(payload, "__len__") else -1,
            )
            output = {"resource_sync": payload}
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def comms_mock_call(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "comms_mock_call"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            incident = dict(fetched.get("incident", {}))
            call_payload = {
                "incident_id": run.incident_id,
                "summary": str(incident.get("description", "No summary available")),
            }
            response = json.loads(self._mcp.call_tool("call_user_for_incident_info", call_payload))
            trace_event("orchestration.comms.mocked", run_id=run.id, summary=response.get("summary"))
            self._complete_step(run, step, name, response, mark_run_completed=True)
            return {name: response}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise
