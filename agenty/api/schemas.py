"""API request/response models for orchestration endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StartOrchestrationRequest(BaseModel):
    incident_id: str = Field(min_length=1)
    org_id: str = Field(min_length=1)


class StartOrchestrationResponse(BaseModel):
    run_id: str
    status: str
