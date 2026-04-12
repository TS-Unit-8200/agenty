"""Incident intake/report routes: persist incident, optionally start orchestration."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from agenty.agent import AgentRuntime
from agenty.api.access_log import agenty_echo
from agenty.api.schemas import (
    IncidentReportLocation,
    IncidentReportRequest,
    IncidentReportResponse,
    IntakeNarrativeRequest,
)
from agenty.config import Settings
from agenty.orchestration.engine import OrchestrationEngine
from agenty.orchestration.incident_intake import (
    _resolve_coords,
    build_mongo_incident_document,
    draft_incident_from_narrative_llm,
    enrich_report_with_llm,
    new_incident_id,
)
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.tracing import trace_event

logger = logging.getLogger(__name__)


def create_incident_report_router(
    *,
    engine: OrchestrationEngine,
    repository: OrchestrationRepository,
    runtime: AgentRuntime,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/orchestrations", tags=["orchestrations"])

    @router.post("/intake", response_model=IncidentReportResponse)
    async def narrative_intake_and_start(request: IntakeNarrativeRequest) -> IncidentReportResponse:
        org_ext = (request.organization_external_id or "").strip() or settings.intake_default_organization_external_id
        workflow_org = (request.workflow_org_id or "").strip() or org_ext
        narrative = request.narrative.strip()
        agenty_echo(
            f"[agenty] handler POST /orchestrations/intake - narrative LLM + Mongo append + start run; "
            f"narrative_chars={len(narrative)} lat={request.lat!r} lng={request.lng!r} org={org_ext!r}",
        )
        logger.info(
            "api.intake POST /orchestrations/intake chars=%s lat=%s lng=%s org=%s",
            len(narrative),
            request.lat,
            request.lng,
            org_ext,
        )
        draft = await asyncio.to_thread(
            draft_incident_from_narrative_llm,
            runtime,
            narrative,
            lat_hint=request.lat,
            lng_hint=request.lng,
            execution_mode=request.execution_mode,
        )
        agenty_echo(
            f"[agenty] handler POST /orchestrations/intake - LLM draft done: "
            f"title={draft.title[:80]!r} type={draft.type!r} powiat={draft.powiat!r}",
        )
        lat, lng, gmina = _resolve_coords(draft.powiat, draft.gmina, request.lat, request.lng)
        address = (draft.address or "").strip() or f"Zgloszenie kryzysowe, {gmina}"
        inc_type = draft.type.strip().lower()
        pri = draft.priority.strip().lower()
        incident_id = new_incident_id()
        doc = build_mongo_incident_document(
            incident_id=incident_id,
            title=draft.title,
            description=draft.description.strip(),
            incident_type=inc_type,
            priority=pri,
            powiat=draft.powiat.strip(),
            gmina=gmina,
            lat=lat,
            lng=lng,
            address=address,
            voivodeship=(draft.voivodeship or "lubelskie").strip(),
            affected_population=draft.affected_population,
        )
        try:
            repository.append_incident_to_organization(org_ext, doc)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        agenty_echo(
            f"[agenty] handler POST /orchestrations/intake - Mongo: appended incident_id={incident_id!r} "
            f"to organization external_id={org_ext!r}",
        )
        trace_event(
            "api.incident.intake",
            incident_id=incident_id,
            organization_external_id=org_ext,
            workflow_org_id=workflow_org,
            autostart=request.autostart,
            execution_mode=request.execution_mode,
        )
        logger.info(
            "api.intake persisted incident_id=%s title=%r type=%s priority=%s",
            incident_id,
            doc["title"][:120],
            doc["type"],
            doc["priority"],
        )

        run_id: str | None = None
        status = "saved"
        if request.autostart:
            run = engine.start_run(
                incident_id=incident_id,
                org_id=workflow_org,
                execution_mode=request.execution_mode,
            )
            trace_event(
                "api.orchestration.start",
                run_id=run.id,
                incident_id=incident_id,
                org_id=workflow_org,
                execution_mode=request.execution_mode,
            )
            scheduled = engine.schedule(run.id)
            logger.info("api.intake started run_id=%s for incident_id=%s", run.id, incident_id)
            agenty_echo(
                f"[agenty] handler POST /orchestrations/intake - returning HTTP 200 with incident_id={incident_id!r} "
                f"run_id={run.id!r}; scheduled={scheduled}",
            )
            run_id = run.id
            status = run.status
        else:
            agenty_echo(
                f"[agenty] handler POST /orchestrations/intake - returning saved incident_id={incident_id!r} without autostart",
            )

        return IncidentReportResponse(
            incident_id=incident_id,
            run_id=run_id,
            status=status,
            title=doc["title"],
            description=doc["description"],
            type=doc["type"],
            priority=doc["priority"],
            affected_population=int(doc["affected_population"]),
            location=IncidentReportLocation(
                lat=lat,
                lng=lng,
                powiat=draft.powiat.strip(),
                gmina=gmina,
                address=address,
            ),
        )

    @router.post("/report", response_model=IncidentReportResponse)
    async def report_and_start(request: IncidentReportRequest) -> IncidentReportResponse:
        org_ext = (request.organization_external_id or "").strip() or settings.intake_default_organization_external_id
        workflow_org = (request.workflow_org_id or "").strip() or org_ext
        agenty_echo(
            f"[agenty] handler POST /orchestrations/report - structured report + LLM enrich + Mongo; "
            f"title={request.title[:60]!r} org={org_ext!r}",
        )

        lat, lng, gmina = _resolve_coords(request.powiat, request.gmina, request.lat, request.lng)
        address = (request.address or "").strip() or f"Zgloszenie kryzysowe, {gmina}"

        llm_payload = {
            "title": request.title,
            "description": request.description,
            "type": request.type,
            "priority": request.priority,
            "powiat": request.powiat,
            "gmina": gmina,
            "address": address,
        }
        agenty_echo("[agenty] handler POST /orchestrations/report - calling enrich_report_with_llm ...")
        enriched = enrich_report_with_llm(
            runtime,
            llm_payload,
            execution_mode=request.execution_mode,
        )
        narrative = (enriched.narrative_summary or "").strip()
        description = request.description.strip()
        if narrative:
            description = f"{description}\n\n[Synteza modelu]\n{narrative}"

        incident_id = new_incident_id()
        doc = build_mongo_incident_document(
            incident_id=incident_id,
            title=request.title,
            description=description,
            incident_type=request.type.strip(),
            priority=request.priority.strip(),
            powiat=request.powiat.strip(),
            gmina=gmina,
            lat=lat,
            lng=lng,
            address=address,
            voivodeship=enriched.voivodeship.strip() or "lubelskie",
            affected_population=enriched.affected_population,
        )
        try:
            repository.append_incident_to_organization(org_ext, doc)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        trace_event(
            "api.incident.reported",
            incident_id=incident_id,
            organization_external_id=org_ext,
            workflow_org_id=workflow_org,
            autostart=request.autostart,
            execution_mode=request.execution_mode,
        )

        run_id: str | None = None
        status = "saved"
        if request.autostart:
            run = engine.start_run(
                incident_id=incident_id,
                org_id=workflow_org,
                execution_mode=request.execution_mode,
            )
            trace_event(
                "api.orchestration.start",
                run_id=run.id,
                incident_id=incident_id,
                org_id=workflow_org,
                execution_mode=request.execution_mode,
            )
            scheduled = engine.schedule(run.id)
            agenty_echo(
                f"[agenty] handler POST /orchestrations/report - returning incident_id={incident_id!r} "
                f"run_id={run.id!r}; scheduled={scheduled}",
            )
            run_id = run.id
            status = run.status
        else:
            agenty_echo(
                f"[agenty] handler POST /orchestrations/report - returning saved incident_id={incident_id!r} without autostart",
            )

        return IncidentReportResponse(
            incident_id=incident_id,
            run_id=run_id,
            status=status,
            title=doc["title"],
            description=doc["description"],
            type=doc["type"],
            priority=doc["priority"],
            affected_population=int(doc["affected_population"]),
            location=IncidentReportLocation(
                lat=lat,
                lng=lng,
                powiat=request.powiat.strip(),
                gmina=gmina,
                address=address,
            ),
        )

    return router
