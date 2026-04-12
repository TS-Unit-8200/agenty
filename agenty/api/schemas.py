"""API request/response models for orchestration endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExecutionMode = Literal["default", "cloud_fallback"]


class StartOrchestrationRequest(BaseModel):
    incident_id: str = Field(min_length=1)
    org_id: str = Field(min_length=1)
    execution_mode: ExecutionMode = "default"


class StartOrchestrationResponse(BaseModel):
    run_id: str
    status: str


class IncidentReportRequest(BaseModel):
    """Citizen / operator incident report from the Next.js UI."""

    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=20_000)
    type: str = Field(min_length=1, max_length=64, description="e.g. blackout, flood, chemical, accident, other")
    priority: str = Field(min_length=1, max_length=32)
    powiat: str = Field(min_length=2, max_length=80)
    gmina: str | None = Field(default=None, max_length=120)
    lat: float | None = None
    lng: float | None = None
    address: str | None = Field(default=None, max_length=500)
    organization_external_id: str | None = Field(
        default=None,
        description="Mongo organizations.external_id; defaults to INTAKE_DEFAULT_ORG_EXTERNAL_ID",
    )
    workflow_org_id: str | None = Field(
        default=None,
        description="Value stored on the workflow run (org_id); defaults to organization_external_id",
    )
    autostart: bool = True
    execution_mode: ExecutionMode = "default"


class IncidentReportLocation(BaseModel):
    lat: float
    lng: float
    powiat: str
    gmina: str
    address: str


class IncidentReportResponse(BaseModel):
    incident_id: str
    run_id: str | None = None
    status: str
    title: str
    description: str
    type: str
    priority: str
    affected_population: int
    location: IncidentReportLocation


class IntakeNarrativeRequest(BaseModel):
    """Raw operator narrative; the LLM drafts the full incident and the server persists + starts the run."""

    narrative: str = Field(min_length=20, max_length=50_000)
    lat: float | None = None
    lng: float | None = None
    organization_external_id: str | None = Field(
        default=None,
        description="Mongo organizations.external_id; defaults to INTAKE_DEFAULT_ORG_EXTERNAL_ID",
    )
    workflow_org_id: str | None = Field(
        default=None,
        description="Stored on the workflow run; defaults to organization_external_id",
    )
    autostart: bool = True
    execution_mode: ExecutionMode = "default"
