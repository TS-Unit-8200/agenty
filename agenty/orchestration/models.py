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


class OrchestrationResult(BaseModel):
    run: WorkflowRun
    steps: list[WorkflowStep] = Field(default_factory=list)
    agent_runs: list[AgentRun] = Field(default_factory=list)
    scenario_version: ScenarioVersion | None = None
    comms_summary: str | None = None
    orchestrator_report: str | None = None
