"""Reconciles agent outputs into agreements/conflicts/gaps."""

from __future__ import annotations

from typing import Any

from agenty.orchestration.models import AgentRun


class ReconciliationService:
    def reconcile(self, agent_runs: list[AgentRun]) -> dict[str, Any]:
        successful = [run for run in agent_runs if run.status == "completed" and run.response]
        failed = [run for run in agent_runs if run.status != "completed"]

        agreements: list[str] = []
        conflicts: list[dict[str, str]] = []
        gaps: list[str] = []

        for run in successful[:3]:
            agreements.append(f"{run.agent_id}: {run.response.splitlines()[0][:160]}")

        if failed:
            gaps.append("Some agent perspectives are missing due to failures/timeouts.")
            for run in failed:
                conflicts.append(
                    {
                        "conflict": "Missing perspective",
                        "side_a": run.agent_id,
                        "side_b": "orchestrator",
                        "essence": run.error or "No response",
                    }
                )

        if not agreements:
            gaps.append("No successful agent output available.")

        return {
            "agreements": agreements,
            "conflicts": conflicts,
            "gaps": gaps,
            "agent_summary": [
                {
                    "agent_id": run.agent_id,
                    "status": run.status,
                    "response": run.response,
                    "error": run.error,
                }
                for run in agent_runs
            ],
        }
