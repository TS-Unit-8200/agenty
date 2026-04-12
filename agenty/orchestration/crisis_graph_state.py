"""LangGraph state for the crisis council workflow (mirrors legacy step payload keys)."""

from __future__ import annotations

from typing import Any, TypedDict


class CrisisGraphState(TypedDict, total=False):
    """Keys match historical ``state_payload`` step names for traceability."""

    run_id: str
    incident_id: str
    org_id: str
    fetch_hierarchy: dict[str, Any]
    select_agents: dict[str, Any]
    run_agents_async: dict[str, Any]
    resolve_conflicts: dict[str, Any]
    plan_external_info: dict[str, Any]
    await_external_info: dict[str, Any]
    refresh_agent_after_call: dict[str, Any]
    run_orchestrator: dict[str, Any]
    generate_scenarios: dict[str, Any]
    sync_resources: dict[str, Any]
    comms_mock_call: dict[str, Any]
