"""Domain models for incident orchestration workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from agenty.db.models import Scenario

WorkflowState = Literal[
    "created",
    "fetch_hierarchy",
    "select_agents",
    "run_agents_async",
    "resolve_conflicts",
    "plan_external_info",
    "await_external_info",
    "refresh_agent_after_call",
    "run_orchestrator",
    "generate_scenarios",
    "sync_resources",
    "comms_mock_call",
    "completed",
    "failed",
    "retrying",
    "partial_completed",
]

StepStatus = Literal["pending", "running", "completed", "failed", "skipped"]
AgentRunStatus = Literal["completed", "failed", "timed_out"]
AgentUrgency = Literal["immediate", "hours", "days"]
ExternalInfoStatus = Literal[
    "planned",
    "initiated",
    "waiting",
    "completed",
    "failed",
    "timed_out",
    "skipped",
]


class WorkflowRun(BaseModel):
    id: str
    incident_id: str
    org_id: str
    orchestrator_version: str
    status: WorkflowState
    current_state: WorkflowState
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    last_error: str | None = None


class WorkflowStep(BaseModel):
    run_id: str
    state: WorkflowState
    status: StepStatus
    attempts: int = 0
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AgentRunSummary(BaseModel):
    perspective: str
    concerns: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    urgency: AgentUrgency = "hours"


class AgentRun(BaseModel):
    run_id: str
    agent_id: str
    status: AgentRunStatus
    started_at: datetime
    finished_at: datetime
    latency_ms: int
    response: str | None = None
    error: str | None = None
    summary: AgentRunSummary | None = None


class ScenarioVersion(BaseModel):
    id: str
    run_id: str
    incident_id: str
    created_at: datetime
    recommendation_label: str
    confidence: float
    scenarios: list[Scenario] = Field(default_factory=list)
    rationale: str = ""


class ExternalInfoRequest(BaseModel):
    id: str
    run_id: str
    incident_id: str
    resource_id: str
    resource_name: str
    phone_number: str
    resource_type: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None
    owner_agent_id: str | None = None
    call_id: str | None = None
    schema_def: dict[str, Any] = Field(default_factory=dict)
    requirements: str = ""
    reason: str | None = None
    status: ExternalInfoStatus
    notice: str | None = None
    result: dict[str, Any] | None = None
    transcript_excerpt: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class OrchestrationResult(BaseModel):
    run: WorkflowRun
    steps: list[WorkflowStep] = Field(default_factory=list)
    agent_runs: list[AgentRun] = Field(default_factory=list)
    scenario_version: ScenarioVersion | None = None
    comms_summary: str | None = None
    orchestrator_report: str | None = None
    external_info: ExternalInfoRequest | None = None
