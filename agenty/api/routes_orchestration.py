"""FastAPI routes for orchestration lifecycle."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from agenty.api.access_log import agenty_echo
from agenty.api.schemas import StartOrchestrationRequest, StartOrchestrationResponse
from agenty.orchestration.engine import OrchestrationEngine
from agenty.orchestration.models import AgentRun
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.state_machine import TERMINAL_STATES
from agenty.orchestration.tracing import trace_event


def _scenario_version_id_from_steps(steps: list[object]) -> str | None:
    for step in steps:
        state = getattr(step, "state", None)
        output_payload = getattr(step, "output_payload", {})
        if state == "generate_scenarios" and isinstance(output_payload, dict):
            value = output_payload.get("scenario_version_id")
            return str(value) if value else None
    return None


def _latest_visible_agent_runs(agent_runs: list[AgentRun | object]) -> list[AgentRun | object]:
    latest: dict[str, AgentRun | object] = {}
    order: list[str] = []
    for item in agent_runs:
        agent_id = str(getattr(item, "agent_id", "") or "")
        if not agent_id:
            continue
        if agent_id not in order:
            order.append(agent_id)
        latest[agent_id] = item
    return [latest[agent_id] for agent_id in order if agent_id in latest]


def create_orchestration_router(
    *,
    engine: OrchestrationEngine,
    repository: OrchestrationRepository,
) -> APIRouter:
    router = APIRouter(prefix="/orchestrations", tags=["orchestrations"])

    @router.post("", response_model=StartOrchestrationResponse)
    async def start_orchestration(request: StartOrchestrationRequest) -> StartOrchestrationResponse:
        agenty_echo(
            f"[agenty] handler POST /orchestrations - start workflow for "
            f"incident_id={request.incident_id!r} org_id={request.org_id!r}",
        )
        run, reused = engine.ensure_run(
            incident_id=request.incident_id,
            org_id=request.org_id,
            execution_mode=request.execution_mode,
        )
        trace_event(
            "api.orchestration.start",
            run_id=run.id,
            incident_id=request.incident_id,
            org_id=request.org_id,
            execution_mode=request.execution_mode,
            reused=reused,
        )
        scheduled = engine.schedule(run.id, resume=reused)
        agenty_echo(
            f"[agenty] handler POST /orchestrations - returning run_id={run.id} status={run.status!r} "
            f"reused={reused} scheduled={scheduled}",
        )
        return StartOrchestrationResponse(run_id=run.id, status=run.status)

    @router.post("/{run_id}/resume", response_model=StartOrchestrationResponse)
    async def resume_orchestration(run_id: str) -> StartOrchestrationResponse:
        agenty_echo(f"[agenty] handler POST /orchestrations/{run_id}/resume - resume requested")
        run = repository.get_run(run_id)
        if run is None:
            agenty_echo(f"[agenty] handler POST /orchestrations/{run_id}/resume - 404 run not found")
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status in TERMINAL_STATES:
            agenty_echo(
                f"[agenty] handler POST /orchestrations/{run_id}/resume - no-op terminal run status={run.status!r}",
            )
            return StartOrchestrationResponse(run_id=run.id, status=run.status)

        scheduled = engine.schedule(run.id, resume=True)
        trace_event("api.orchestration.resume", run_id=run.id, scheduled=scheduled, current_state=run.current_state)
        agenty_echo(
            f"[agenty] handler POST /orchestrations/{run_id}/resume - "
            f"scheduled={scheduled} status={run.status!r} current_state={run.current_state!r}",
        )
        return StartOrchestrationResponse(run_id=run.id, status=run.status)

    @router.get("/{run_id}")
    async def get_orchestration(run_id: str) -> dict[str, object]:
        agenty_echo(
            f"[agenty] handler GET /orchestrations/{{run_id}} - loading run + steps + agent_runs from Mongo "
            f"(run_id={run_id!r})",
        )
        run = repository.get_run(run_id)
        if run is None:
            agenty_echo(f"[agenty] handler GET /orchestrations/{run_id} - 404 run not found")
            raise HTTPException(status_code=404, detail="Run not found")
        trace_event("api.orchestration.status", run_id=run_id, status=run.status)
        steps = repository.list_steps(run_id)
        agent_runs = repository.list_agent_runs(run_id)
        agenty_echo(
            f"[agenty] handler GET /orchestrations/{run_id} - "
            f"run status={run.status!r} current_state={run.current_state!r} "
            f"steps={len(steps)} agent_runs={len(agent_runs)}",
        )
        return {
            "run": run.model_dump(mode="json"),
            "steps": [item.model_dump(mode="json") for item in steps],
            "agent_runs": [item.model_dump(mode="json") for item in agent_runs],
        }

    @router.get("/{run_id}/result")
    async def get_orchestration_result(
        run_id: str,
        include_steps: bool = Query(default=False),
    ) -> dict[str, object]:
        agenty_echo(
            f"[agenty] handler GET /orchestrations/{{run_id}}/result - canonical snapshot "
            f"(run_id={run_id!r})",
        )
        run = repository.get_run(run_id)
        if run is None:
            agenty_echo(f"[agenty] handler GET /orchestrations/{run_id}/result - 404 run not found")
            raise HTTPException(status_code=404, detail="Run not found")
        trace_event("api.orchestration.result", run_id=run_id, status=run.status)
        steps = repository.list_steps(run_id)
        agent_runs = repository.list_agent_runs(run_id)
        scenario_version_id = _scenario_version_id_from_steps(steps)
        scenario_version = repository.get_scenario_version(scenario_version_id) if scenario_version_id else None
        external_info = repository.get_external_info_request(run_id)
        visible_agent_runs = _latest_visible_agent_runs(agent_runs)
        orchestrator_report = None
        for agent_run in reversed(visible_agent_runs):
            if agent_run.agent_id == "orchestrator" and agent_run.response:
                orchestrator_report = agent_run.response
                break
        agenty_echo(
            f"[agenty] handler GET /orchestrations/{run_id}/result - "
            f"run status={run.status!r} scenario_version_id={scenario_version_id!r} "
            f"agent_runs={len(visible_agent_runs)} has_scenario={scenario_version is not None} include_steps={include_steps}",
        )
        payload: dict[str, object] = {
            "run": run.model_dump(mode="json"),
            "agent_runs": [item.model_dump(mode="json") for item in visible_agent_runs],
            "scenario_version": scenario_version.model_dump(mode="json") if scenario_version else None,
            "orchestrator_report": orchestrator_report,
            "external_info": external_info.model_dump(mode="json") if external_info else None,
        }
        if include_steps:
            payload["steps"] = [item.model_dump(mode="json") for item in steps]
        return payload

    return router
