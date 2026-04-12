"""Utilities for repairing duplicate or stale orchestration runs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from agenty.orchestration.models import WorkflowRun
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.state_machine import TERMINAL_STATES


@dataclass(frozen=True)
class RepairDecision:
    incident_id: str
    orchestrator_version: str
    canonical_run_id: str
    superseded_run_ids: tuple[str, ...]
    needs_resume: bool
    scenario_version_id: str | None


def _scenario_version_id(repository: OrchestrationRepository, run_id: str) -> str | None:
    for step in repository.list_steps(run_id):
        if step.state == "generate_scenarios":
            value = step.output_payload.get("scenario_version_id")
            return str(value) if value else None
    return None


def _has_orchestrator_report(repository: OrchestrationRepository, run_id: str) -> bool:
    return any(
        item.agent_id == "orchestrator" and bool(item.response)
        for item in repository.list_agent_runs(run_id)
    )


def _rank_run(repository: OrchestrationRepository, run: WorkflowRun) -> tuple[int, int, int, object]:
    steps = repository.list_steps(run.id)
    completed_steps = sum(1 for step in steps if step.status == "completed")
    scenario_rank = 1 if _scenario_version_id(repository, run.id) else 0
    orchestrator_rank = 1 if _has_orchestrator_report(repository, run.id) else 0
    return (scenario_rank, orchestrator_rank, completed_steps, run.updated_at)


def repair_duplicate_runs(
    repository: OrchestrationRepository,
    *,
    incident_id: str | None = None,
    orchestrator_version: str | None = None,
) -> list[RepairDecision]:
    groups: dict[tuple[str, str], list[WorkflowRun]] = defaultdict(list)
    for run in repository.list_runs(incident_id=incident_id, orchestrator_version=orchestrator_version):
        groups[(run.incident_id, run.orchestrator_version)].append(run)

    decisions: list[RepairDecision] = []
    for (group_incident_id, group_version), runs in groups.items():
        canonical = sorted(runs, key=lambda item: _rank_run(repository, item), reverse=True)[0]
        scenario_version_id = _scenario_version_id(repository, canonical.id)
        repository.update_incident_links(
            group_incident_id,
            run_id=canonical.id,
            scenario_version_id=scenario_version_id,
        )

        superseded: list[str] = []
        for run in runs:
            if run.id == canonical.id:
                continue
            if run.status not in TERMINAL_STATES:
                repository.mark_run_superseded(run.id, superseded_by=canonical.id)
                superseded.append(run.id)

        decisions.append(
            RepairDecision(
                incident_id=group_incident_id,
                orchestrator_version=group_version,
                canonical_run_id=canonical.id,
                superseded_run_ids=tuple(sorted(superseded)),
                needs_resume=canonical.status not in TERMINAL_STATES,
                scenario_version_id=scenario_version_id,
            )
        )

    return decisions
