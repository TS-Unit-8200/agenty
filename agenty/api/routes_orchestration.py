"""FastAPI routes for orchestration lifecycle."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from agenty.api.access_log import agenty_echo
from agenty.api.schemas import StartOrchestrationRequest, StartOrchestrationResponse
from agenty.orchestration.engine import OrchestrationEngine
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.tracing import trace_event

logger = logging.getLogger(__name__)


def _orchestration_task_done_callback(run_id: str) -> Callable[[asyncio.Task[object]], None]:
    """Return a done-callback that logs success/failure of ``engine.execute`` to stderr."""

    def _cb(task: asyncio.Task[object]) -> None:
        try:
            _ = task.result()
            agenty_echo(
                f"[agenty] background LangGraph execute(run_id={run_id}) — finished OK "
                f"(orchestration steps persisted in Mongo)",
            )
        except Exception as exc:  # noqa: BLE001
            agenty_echo(f"[agenty] background LangGraph execute(run_id={run_id}) — FAILED: {exc!r}")
            logger.exception("Background orchestration task failed run_id=%s", run_id)

    return _cb


def create_orchestration_router(
    *,
    engine: OrchestrationEngine,
    repository: OrchestrationRepository,
) -> APIRouter:
    router = APIRouter(prefix="/orchestrations", tags=["orchestrations"])

    @router.post("", response_model=StartOrchestrationResponse)
    async def start_orchestration(request: StartOrchestrationRequest) -> StartOrchestrationResponse:
        agenty_echo(
            f"[agenty] handler POST /orchestrations — start workflow for "
            f"incident_id={request.incident_id!r} org_id={request.org_id!r}",
        )
        run = engine.start_run(incident_id=request.incident_id, org_id=request.org_id)
        trace_event("api.orchestration.start", run_id=run.id, incident_id=request.incident_id, org_id=request.org_id)
        agenty_echo(
            f"[agenty] handler POST /orchestrations — created run_id={run.id}; "
            f"scheduling async LangGraph pipeline (response returns immediately)",
        )
        task = asyncio.create_task(engine.execute(run.id))
        task.add_done_callback(_orchestration_task_done_callback(run.id))
        agenty_echo(
            f"[agenty] handler POST /orchestrations — returning run_id={run.id} status={run.status!r}",
        )
        return StartOrchestrationResponse(run_id=run.id, status=run.status)

    @router.get("/{run_id}")
    async def get_orchestration(run_id: str) -> dict[str, object]:
        agenty_echo(
            f"[agenty] handler GET /orchestrations/{{run_id}} — loading run + steps + agent_runs from Mongo "
            f"(run_id={run_id!r})",
        )
        run = repository.get_run(run_id)
        if run is None:
            agenty_echo(f"[agenty] handler GET /orchestrations/{run_id} — 404 run not found")
            raise HTTPException(status_code=404, detail="Run not found")
        trace_event("api.orchestration.status", run_id=run_id, status=run.status)
        steps = repository.list_steps(run_id)
        agent_runs = repository.list_agent_runs(run_id)
        agenty_echo(
            f"[agenty] handler GET /orchestrations/{run_id} — "
            f"run status={run.status!r} current_state={run.current_state!r} "
            f"steps={len(steps)} agent_runs={len(agent_runs)}",
        )
        return {
            "run": run.model_dump(),
            "steps": [item.model_dump() for item in steps],
            "agent_runs": [item.model_dump() for item in agent_runs],
        }

    @router.get("/{run_id}/result")
    async def get_orchestration_result(run_id: str) -> dict[str, object]:
        agenty_echo(
            f"[agenty] handler GET /orchestrations/{{run_id}}/result — full snapshot + scenario_version "
            f"(run_id={run_id!r})",
        )
        run = repository.get_run(run_id)
        if run is None:
            agenty_echo(f"[agenty] handler GET /orchestrations/{run_id}/result — 404 run not found")
            raise HTTPException(status_code=404, detail="Run not found")
        trace_event("api.orchestration.result", run_id=run_id, status=run.status)
        steps = repository.list_steps(run_id)
        scenario_version_id = None
        for step in steps:
            if step.state == "generate_scenarios":
                scenario_version_id = step.output_payload.get("scenario_version_id")
        scenario_version = repository.get_scenario_version(str(scenario_version_id)) if scenario_version_id else None
        agenty_echo(
            f"[agenty] handler GET /orchestrations/{run_id}/result — "
            f"run status={run.status!r} scenario_version_id={scenario_version_id!r} "
            f"has_scenario={scenario_version is not None}",
        )
        return {
            "run": run.model_dump(),
            "steps": [item.model_dump() for item in steps],
            "scenario_version": scenario_version.model_dump() if scenario_version else None,
        }

    return router
