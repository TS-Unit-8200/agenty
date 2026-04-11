"""FastAPI routes for orchestration lifecycle."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from agenty.api.schemas import StartOrchestrationRequest, StartOrchestrationResponse
from agenty.orchestration.engine import OrchestrationEngine
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.tracing import trace_event

logger = logging.getLogger(__name__)


def create_orchestration_router(
    *,
    engine: OrchestrationEngine,
    repository: OrchestrationRepository,
) -> APIRouter:
    router = APIRouter(prefix="/orchestrations", tags=["orchestrations"])

    @router.post("", response_model=StartOrchestrationResponse)
    async def start_orchestration(request: StartOrchestrationRequest) -> StartOrchestrationResponse:
        run = engine.start_run(incident_id=request.incident_id, org_id=request.org_id)
        trace_event("api.orchestration.start", run_id=run.id, incident_id=request.incident_id, org_id=request.org_id)
        task = asyncio.create_task(engine.execute(run.id))
        task.add_done_callback(_log_background_failure)
        return StartOrchestrationResponse(run_id=run.id, status=run.status)

    @router.get("/{run_id}")
    async def get_orchestration(run_id: str) -> dict[str, object]:
        run = repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        trace_event("api.orchestration.status", run_id=run_id, status=run.status)
        steps = repository.list_steps(run_id)
        return {
            "run": run.model_dump(),
            "steps": [item.model_dump() for item in steps],
        }

    @router.get("/{run_id}/result")
    async def get_orchestration_result(run_id: str) -> dict[str, object]:
        run = repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        trace_event("api.orchestration.result", run_id=run_id, status=run.status)
        steps = repository.list_steps(run_id)
        scenario_version_id = None
        for step in steps:
            if step.state == "generate_scenarios":
                scenario_version_id = step.output_payload.get("scenario_version_id")
        scenario_version = repository.get_scenario_version(str(scenario_version_id)) if scenario_version_id else None
        return {
            "run": run.model_dump(),
            "steps": [item.model_dump() for item in steps],
            "scenario_version": scenario_version.model_dump() if scenario_version else None,
        }

    return router


def _log_background_failure(task: asyncio.Task[object]) -> None:
    try:
        _ = task.result()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Background orchestration task failed: %s", exc)
