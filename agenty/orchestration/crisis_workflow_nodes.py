"""LangGraph node bodies: Mongo step tracing + council execution + orchestrator synthesis."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
import asyncio

from agenty.orchestration.agent_runner import AgentRunner
from agenty.orchestration.agent_selector import AgentSelector
from agenty.orchestration.crisis_graph_state import CrisisGraphState
from agenty.orchestration.hierarchy_service import HierarchyService
from agenty.orchestration.models import AgentRun, AgentRunSummary, WorkflowRun, WorkflowState, WorkflowStep
from agenty.orchestration.reconciliation import ReconciliationService
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.response_parsers import summarize_agent_response
from agenty.orchestration.scenario_service import ScenarioService
from agenty.orchestration.tracing import trace_event, trace_human_block

if TYPE_CHECKING:
    from agenty.mcp_gateway.base import MCPGateway

logger = logging.getLogger(__name__)

_COUNCIL_INSTRUCTION = (
    "Jestes czlonkiem rady agentow CrisisTwin. Pozostale role z sekcji 'Rada' "
    "otrzymuja ten sam incydent i odpowiadaja rownolegle. Po zebraniu glosow "
    "osobny orchestrator porowna zgodnosci, konflikty i zaleznosci. "
    "Skup sie na swojej roli, liczbach, ryzykach i priorytetach."
)

_ORCHESTRATOR_PROMPT = (
    "Na podstawie incydentu, odpowiedzi rady i rekonsyliacji przygotuj pelny raport "
    "orchestratora zgodnie z instrukcja systemowa. "
    "Bazuj przede wszystkim na streszczeniach operacyjnych rady. "
    "Do fragmentow zrodlowych siegaj tylko wtedy, gdy streszczenie jest niepelne albo agent nie odpowiedzial. "
    "Nie wymyslaj danych spoza materialu. Kazda liczbe oznacz jako WIADOME, SZACUNEK albo NIEZNANE, "
    "a scenariusze zbuduj wylacznie z roznic i decyzji wynikajacych z tej rady."
)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


def _step_input_payload(state: CrisisGraphState, step: WorkflowState) -> dict[str, Any]:
    if step == "fetch_hierarchy":
        return {}
    order: list[WorkflowState] = [
        "fetch_hierarchy",
        "select_agents",
        "run_agents_async",
        "resolve_conflicts",
        "run_orchestrator",
        "generate_scenarios",
        "sync_resources",
        "comms_mock_call",
    ]
    idx = order.index(step)
    if idx == 0:
        return {}
    prev_key = order[idx - 1]
    previous = state.get(prev_key)
    return _json_safe(previous) if isinstance(previous, dict) else {}


def _summary_payload(summary: AgentRunSummary | None) -> dict[str, Any] | None:
    return summary.model_dump(mode="json") if summary else None


def _truncate(text: str, *, limit: int = 12_000) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n... [truncated]"


def _render_council_sources(agent_runs: list[AgentRun]) -> str:
    blocks: list[str] = []
    for run in agent_runs:
        lines = [
            f"## {run.agent_id}",
            f"status: {run.status}",
        ]

        if run.summary:
            lines.extend(
                [
                    f"perspective: {run.summary.perspective}",
                    "concerns:",
                    *[f"- {item}" for item in run.summary.concerns[:3]],
                    "recommendations:",
                    *[f"- {item}" for item in run.summary.recommendations[:3]],
                    f"urgency: {run.summary.urgency}",
                ]
            )

        if not run.summary or run.status != "completed":
            body = run.response or run.error or "(empty)"
            lines.extend(
                [
                    "source_excerpt:",
                    _truncate(body, limit=1_500),
                ]
            )

        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


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
        self._heartbeat_interval_s = 5.0

    def _run(self, state: CrisisGraphState) -> WorkflowRun:
        run = self._repository.get_run(state["run_id"])
        if run is None:
            raise KeyError(f"Unknown run id {state['run_id']}")
        return run

    def _begin_step(self, run: WorkflowRun, name: WorkflowState, state: CrisisGraphState) -> WorkflowStep:
        trace_event("orchestration.step.start", run_id=run.id, state=name)
        self._repository.update_run_state(run.id, status=name, current_state=name)
        previous = self._repository.get_step(run.id, name)
        step = WorkflowStep(
            run_id=run.id,
            state=name,
            status="running",
            attempts=(previous.attempts + 1) if previous and previous.status != "completed" else (previous.attempts if previous else 0),
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
        safe_output = _json_safe(output)
        step.status = "completed"
        step.output_payload = safe_output
        step.updated_at = datetime.now(UTC)
        step.finished_at = datetime.now(UTC)
        self._repository.upsert_step(step)
        trace_event("orchestration.step.complete", run_id=run.id, state=name, output_keys=list(safe_output.keys()))
        preview = json.dumps(safe_output, indent=2, ensure_ascii=False)
        if len(preview) > 14_000:
            preview = preview[:14_000] + "\n... [truncated]"
        trace_human_block(
            f"Workflow step '{name}' complete  |  run {run.id}",
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

    def _fail_step(
        self,
        run: WorkflowRun,
        step: WorkflowStep,
        name: WorkflowState,
        exc: BaseException,
        *,
        fail_run: bool = True,
    ) -> None:
        step.status = "failed"
        step.error = str(exc)
        step.updated_at = datetime.now(UTC)
        step.finished_at = datetime.now(UTC)
        self._repository.upsert_step(step)
        if fail_run:
            self._repository.update_run_state(
                run.id,
                status="failed",
                current_state="failed",
                last_error=str(exc),
                completed=True,
            )
        else:
            self._repository.update_run_state(
                run.id,
                status=name,
                current_state=name,
                last_error=str(exc),
                completed=False,
            )
        trace_human_block(
            f"Workflow step '{name}' FAILED  |  run {run.id}",
            str(exc),
        )

    async def _heartbeat_loop(self, run_id: str, name: WorkflowState, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._heartbeat_interval_s)
            except TimeoutError:
                self._repository.touch_step(run_id, name)
                self._repository.touch_run(run_id, current_state=name)

    @asynccontextmanager
    async def _heartbeat(self, run_id: str, name: WorkflowState):
        stop = asyncio.Event()
        task = asyncio.create_task(self._heartbeat_loop(run_id, name, stop))
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            self._repository.touch_step(run_id, name)
            self._repository.touch_run(run_id, current_state=name)

    async def fetch_hierarchy(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "fetch_hierarchy"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            context = self._hierarchy.load_context(run.incident_id)
            trace_event("orchestration.hierarchy.loaded", run_id=run.id, incident_id=run.incident_id)
            output: dict[str, Any] = _json_safe(
                {
                    "organization": context["organization"],
                    "incident": context["incident"],
                    "hierarchy_found": bool(context["hierarchy"]),
                    "hierarchy": context["hierarchy"],
                }
            )
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
                "Incident": json.dumps(incident, ensure_ascii=False, default=_json_default),
                "Organization": json.dumps(
                    fetched.get("organization", {}),
                    ensure_ascii=False,
                    default=_json_default,
                ),
                "Rada": (
                    f"Role w tej turze (odpowiedzi rownolegle): {roster}.\n"
                    f"{_COUNCIL_INSTRUCTION}"
                ),
            }
            prompt = (
                "Przeanalizuj incydent z perspektywy swojej roli. "
                "Podaj najwazniejsze dzialania, ryzyka, zaleznosci czasowe i priorytety. "
                "Uzywaj konkretow, liczb i ograniczen, gdy sa dostepne."
            )
            async with self._heartbeat(run.id, name):
                runs = await self._runner.run(agent_ids, prompt, context_sections)
            for item in runs:
                item.run_id = run.id
                item.summary = summarize_agent_response(
                    agent_id=item.agent_id,
                    response=item.response,
                    error=item.error,
                    status=item.status,
                )
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
                "council_agents": [item.agent_id for item in runs],
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
            agent_runs = [item for item in self._repository.list_agent_runs(run.id) if item.agent_id != "orchestrator"]
            recon = self._reconciliation.reconcile(agent_runs)
            trace_event(
                "orchestration.conflicts.resolved",
                run_id=run.id,
                agreements=len(recon.get("agreements", [])),
                conflicts=len(recon.get("conflicts", [])),
            )
            self._complete_step(run, step, name, recon, mark_run_completed=False)
            return {name: _json_safe(recon)}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def run_orchestrator(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "run_orchestrator"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            recon = state.get("resolve_conflicts")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            if not isinstance(recon, dict):
                raise TypeError("resolve_conflicts payload must be a dict")

            council_runs = [item for item in self._repository.list_agent_runs(run.id) if item.agent_id != "orchestrator"]
            council_summary = [
                {
                    "agent_id": item.agent_id,
                    "status": item.status,
                    "summary": _summary_payload(item.summary),
                    "error": item.error,
                }
                for item in council_runs
            ]
            context_sections = {
                "Incident": json.dumps(fetched.get("incident", {}), ensure_ascii=False, default=_json_default),
                "Organization": json.dumps(fetched.get("organization", {}), ensure_ascii=False, default=_json_default),
                "Rada - streszczenia": json.dumps(council_summary, ensure_ascii=False, indent=2),
                "Rada - odpowiedzi zrodlowe": _render_council_sources(council_runs),
                "Zgodnosci i konflikty": json.dumps(recon, ensure_ascii=False, indent=2, default=_json_default),
            }
            async with self._heartbeat(run.id, name):
                result = await self._runner.run(
                    ["orchestrator"],
                    _ORCHESTRATOR_PROMPT,
                    context_sections,
                    timeout_s=max(180.0, self._runner.default_timeout_s * 3),
                )
            orchestrator_run = result[0]
            orchestrator_run.run_id = run.id
            orchestrator_run.summary = summarize_agent_response(
                agent_id=orchestrator_run.agent_id,
                response=orchestrator_run.response,
                error=orchestrator_run.error,
                status=orchestrator_run.status,
            )
            self._repository.append_agent_run(orchestrator_run)
            output = {
                "agent_id": orchestrator_run.agent_id,
                "status": orchestrator_run.status,
                "has_report": bool(orchestrator_run.response),
                "report_chars": len(orchestrator_run.response or ""),
                "summary": _summary_payload(orchestrator_run.summary),
            }
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            failed_run = AgentRun(
                run_id=run.id,
                agent_id="orchestrator",
                status="failed",
                started_at=step.started_at,
                finished_at=datetime.now(UTC),
                latency_ms=max(0, int((datetime.now(UTC) - step.started_at).total_seconds() * 1000)),
                error=str(exc),
            )
            failed_run.summary = summarize_agent_response(
                agent_id=failed_run.agent_id,
                response=failed_run.response,
                error=failed_run.error,
                status=failed_run.status,
            )
            self._repository.append_agent_run(failed_run)
            self._fail_step(run, step, name, exc, fail_run=False)
            return {
                name: {
                    "agent_id": "orchestrator",
                    "status": "failed",
                    "has_report": False,
                    "report_chars": 0,
                    "summary": _summary_payload(failed_run.summary),
                }
            }

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
            agent_runs = self._repository.list_agent_runs(run.id)
            orchestrator_run = next((item for item in reversed(agent_runs) if item.agent_id == "orchestrator"), None)
            scenario_version = None
            source = "fallback"

            if orchestrator_run and orchestrator_run.status == "completed" and orchestrator_run.response:
                scenario_version = self._scenarios.build_from_orchestrator_report(
                    run_id=run.id,
                    incident_id=run.incident_id,
                    report=orchestrator_run.response,
                    reconciliation=recon,
                )
                if scenario_version is not None:
                    source = "orchestrator"

            if scenario_version is None:
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
                source=source,
            )
            output = {
                "scenario_version_id": scenario_version.id,
                "recommended": scenario_version.recommendation_label,
                "source": source,
                "scenario_count": len(scenario_version.scenarios),
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
            async with self._heartbeat(run.id, name):
                response = await asyncio.to_thread(
                    self._mcp.call_tool,
                    "resource_list",
                    {"incident_id": run.incident_id},
                )
            payload = json.loads(response)
            trace_event(
                "orchestration.resources.synced",
                run_id=run.id,
                resource_count=len(payload) if hasattr(payload, "__len__") else -1,
            )
            output = {"resource_sync": _json_safe(payload)}
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
            async with self._heartbeat(run.id, name):
                raw_response = await asyncio.to_thread(
                    self._mcp.call_tool,
                    "call_user_for_incident_info",
                    call_payload,
                )
            response = json.loads(raw_response)
            trace_event("orchestration.comms.mocked", run_id=run.id, summary=response.get("summary"))
            self._complete_step(run, step, name, response, mark_run_completed=False)
            return {name: _json_safe(response)}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise
