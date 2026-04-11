"""Workflow state transitions and validation."""

from __future__ import annotations

from agenty.orchestration.models import WorkflowState

WORKFLOW_PATH: list[WorkflowState] = [
    "created",
    "fetch_hierarchy",
    "select_agents",
    "run_agents_async",
    "resolve_conflicts",
    "generate_scenarios",
    "sync_resources",
    "comms_mock_call",
    "completed",
]

TERMINAL_STATES: set[WorkflowState] = {"completed", "failed", "partial_completed"}


def next_state(current: WorkflowState) -> WorkflowState:
    if current in TERMINAL_STATES:
        return current
    idx = WORKFLOW_PATH.index(current)
    return WORKFLOW_PATH[idx + 1]


def previous_state(current: WorkflowState) -> WorkflowState:
    if current == "created":
        return "created"
    idx = WORKFLOW_PATH.index(current)
    return WORKFLOW_PATH[idx - 1]


def is_valid_transition(current: WorkflowState, new_state: WorkflowState) -> bool:
    if current == new_state:
        return True
    if current in TERMINAL_STATES:
        return False
    if new_state == "retrying":
        return True
    if new_state == "failed":
        return True
    return next_state(current) == new_state


def resumable_state(current: WorkflowState) -> WorkflowState:
    if current in TERMINAL_STATES:
        return current
    if current == "retrying":
        return "fetch_hierarchy"
    return current
