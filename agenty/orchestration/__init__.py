"""Incident orchestration package with lazy exports to avoid import cycles."""

from __future__ import annotations

from typing import Any

from agenty.orchestration.models import (
    AgentRun,
    OrchestrationResult,
    ScenarioVersion,
    WorkflowRun,
    WorkflowState,
    WorkflowStep,
)

__all__ = [
    "AgentRun",
    "OrchestrationEngine",
    "OrchestrationRepository",
    "OrchestrationResult",
    "ScenarioVersion",
    "WorkflowRun",
    "WorkflowState",
    "WorkflowStep",
]


def __getattr__(name: str) -> Any:
    if name == "OrchestrationEngine":
        from agenty.orchestration.engine import OrchestrationEngine

        return OrchestrationEngine
    if name == "OrchestrationRepository":
        from agenty.orchestration.repository import OrchestrationRepository

        return OrchestrationRepository
    raise AttributeError(name)
