"""Orchestration engine for incident-driven async workflow execution."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from agenty.agent import AgentRuntime
from agenty.mcp_gateway.base import MCPGateway
from agenty.orchestration.agent_runner import AgentRunner
from agenty.orchestration.agent_selector import AgentSelector
from agenty.orchestration.hierarchy_service import HierarchyService
from agenty.orchestration.models import OrchestrationResult, WorkflowRun, WorkflowState, WorkflowStep
from agenty.orchestration.reconciliation import ReconciliationService
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.scenario_service import ScenarioService
from agenty.orchestration.state_machine import TERMINAL_STATES, next_state, previous_state
from agenty.orchestration.tracing import trace_event


class OrchestrationEngine:
    def __init__(
        self,
        *,
        repository: OrchestrationRepository,
        runtime: AgentRuntime,
        mcp: MCPGateway,
        orchestrator_version: str = "v1",
        max_step_retries: int = 2,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._mcp = mcp
        self._orchestrator_version = orchestrator_version
        self._max_step_retries = max_step_retries
        self._hierarchy = HierarchyService(repository)
        self._selector = AgentSelector()
        self._runner = AgentRunner(runtime)
        self._reconciliation = ReconciliationService()
        self._scenarios = ScenarioService(mcp)

    def start_run(self, *, incident_id: str, org_id: str) -> WorkflowRun:
        existing = self._repository.find_existing_run(incident_id, self._orchestrator_version)
        if existing:
            trace_event("orchestration.run.reused", run_id=existing.id, incident_id=incident_id, org_id=org_id)
            return existing
        now = datetime.now(UTC)
        run = WorkflowRun(
            id=str(uuid4()),
            incident_id=incident_id,
            org_id=org_id,
            orchestrator_version=self._orchestrator_version,
            status="created",
            current_state="created",
            started_at=now,
            updated_at=now,
        )
        trace_event("orchestration.run.created", run_id=run.id, incident_id=incident_id, org_id=org_id)
        return self._repository.create_run(run)

    async def execute(self, run_id: str) -> OrchestrationResult:
        run = self._repository.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown run id {run_id}")

        state_payload: dict[str, object] = {}

        while run.current_state not in TERMINAL_STATES:
            current_state = next_state(run.current_state)
            trace_event("orchestration.step.start", run_id=run.id, state=current_state)
            self._repository.update_run_state(
                run.id,
                status=current_state,
                current_state=current_state,
            )

            step = WorkflowStep(
                run_id=run.id,
                state=current_state,
                status="running",
                started_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                input_payload=self._step_input_payload(current_state, state_payload),
            )
            self._repository.upsert_step(step)

            try:
                output = await self._execute_step(run, current_state, state_payload)
                step.status = "completed"
                step.output_payload = output
                step.updated_at = datetime.now(UTC)
                step.finished_at = datetime.now(UTC)
                self._repository.upsert_step(step)
                trace_event("orchestration.step.complete", run_id=run.id, state=current_state, output_keys=list(output.keys()))
                state_payload[current_state] = output

                next_status: WorkflowState = "completed" if current_state == "comms_mock_call" else current_state
                next_cursor = "completed" if current_state == "comms_mock_call" else current_state
                self._repository.update_run_state(
                    run.id,
                    status=next_status,
                    current_state=next_cursor,
                    completed=current_state == "comms_mock_call",
                )
                run = self._repository.get_run(run.id) or run
            except Exception as exc:  # noqa: BLE001
                step.status = "failed"
                step.error = str(exc)
                step.updated_at = datetime.now(UTC)
                step.finished_at = datetime.now(UTC)
                self._repository.upsert_step(step)
                step.attempts += 1
                if step.attempts > self._max_step_retries:
                    self._repository.update_run_state(
                        run.id,
                        status="failed",
                        current_state="failed",
                        last_error=str(exc),
                        completed=True,
                    )
                    break
                self._repository.update_run_state(
                    run.id,
                    status="retrying",
                    current_state=previous_state(current_state),
                    last_error=str(exc),
                )
                trace_event("orchestration.step.retry", run_id=run.id, state=current_state, error=str(exc), attempt=step.attempts)
                await asyncio.sleep(2**step.attempts)
                run = self._repository.get_run(run.id) or run

        latest_run = self._repository.get_run(run.id)
        if latest_run is None:
            raise RuntimeError("Run disappeared while finalizing")

        steps = self._repository.list_steps(latest_run.id)
        agent_runs = self._repository.list_agent_runs(latest_run.id)
        scenario_id = None
        for step in steps:
            if step.state == "generate_scenarios":
                scenario_id = step.output_payload.get("scenario_version_id")
        scenario_version = self._repository.get_scenario_version(str(scenario_id)) if scenario_id else None
        comms_summary = None
        for step in steps:
            if step.state == "comms_mock_call":
                comms_summary = str(step.output_payload.get("summary", ""))

        return OrchestrationResult(
            run=latest_run,
            steps=steps,
            agent_runs=agent_runs,
            scenario_version=scenario_version,
            comms_summary=comms_summary,
        )

    async def _execute_step(
        self,
        run: WorkflowRun,
        state: WorkflowState,
        state_payload: dict[str, object],
    ) -> dict[str, object]:
        if state == "fetch_hierarchy":
            context = self._hierarchy.load_context(run.incident_id)
            trace_event("orchestration.hierarchy.loaded", run_id=run.id, incident_id=run.incident_id)
            return {
                "organization": context["organization"],
                "incident": context["incident"],
                "hierarchy_found": bool(context["hierarchy"]),
                "hierarchy": context["hierarchy"],
            }

        if state == "select_agents":
            fetched = state_payload["fetch_hierarchy"]
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            selected = self._selector.select(
                hierarchy=dict(fetched.get("hierarchy", {})),
                incident=dict(fetched.get("incident", {})),
            )
            trace_event("orchestration.agents.selected", run_id=run.id, agents=selected)
            return {"agents": selected}

        if state == "run_agents_async":
            fetched = state_payload["fetch_hierarchy"]
            selected = state_payload["select_agents"]
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            if not isinstance(selected, dict):
                raise TypeError("select_agents payload must be a dict")
            incident = dict(fetched.get("incident", {}))
            context_sections = {
                "Incident": json.dumps(incident, ensure_ascii=True),
                "Organization": json.dumps(fetched.get("organization", {}), ensure_ascii=True),
            }
            prompt = (
                "Analyze this incident from your role perspective. "
                "Provide key actions, risks, and immediate priorities."
            )
            runs = await self._runner.run(list(selected.get("agents", [])), prompt, context_sections)
            for item in runs:
                item.run_id = run.id
                self._repository.append_agent_run(item)
            trace_event(
                "orchestration.agents.ran",
                run_id=run.id,
                agents=[item.agent_id for item in runs],
                failed=[item.agent_id for item in runs if item.status != "completed"],
            )
            failures = [item for item in runs if item.status != "completed"]
            return {
                "total": len(runs),
                "failures": len(failures),
                "failed_agents": [item.agent_id for item in failures],
            }

        if state == "resolve_conflicts":
            agent_runs = self._repository.list_agent_runs(run.id)
            recon = self._reconciliation.reconcile(agent_runs)
            trace_event("orchestration.conflicts.resolved", run_id=run.id, agreements=len(recon.get("agreements", [])), conflicts=len(recon.get("conflicts", [])))
            return recon

        if state == "generate_scenarios":
            fetched = state_payload["fetch_hierarchy"]
            recon = state_payload["resolve_conflicts"]
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            if not isinstance(recon, dict):
                raise TypeError("resolve_conflicts payload must be a dict")
            incident = dict(fetched.get("incident", {}))
            resource_count = len(incident.get("resources", []))
            scenario_version = self._scenarios.build(
                run_id=run.id,
                incident_id=run.incident_id,
                priority=str(incident.get("priority", "medium")),
                affected_population=int(incident.get("affected_population", 0)),
                resource_count=resource_count,
                reconciliation=recon,
            )
            self._repository.save_scenario_version(scenario_version)
            self._repository.update_incident_links(
                run.incident_id,
                run_id=run.id,
                scenario_version_id=scenario_version.id,
            )
            trace_event("orchestration.scenarios.generated", run_id=run.id, scenario_version_id=scenario_version.id, recommendation=scenario_version.recommendation_label)
            return {
                "scenario_version_id": scenario_version.id,
                "recommended": scenario_version.recommendation_label,
            }

        if state == "sync_resources":
            payload = json.loads(self._mcp.call_tool("resource_list", {"incident_id": run.incident_id}))
            trace_event("orchestration.resources.synced", run_id=run.id, resource_count=len(payload) if hasattr(payload, "__len__") else -1)
            return {"resource_sync": payload}

        if state == "comms_mock_call":
            fetched = state_payload["fetch_hierarchy"]
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            incident = dict(fetched.get("incident", {}))
            call_payload = {
                "incident_id": run.incident_id,
                "summary": str(incident.get("description", "No summary available")),
            }
            response = json.loads(self._mcp.call_tool("call_user_for_incident_info", call_payload))
            trace_event("orchestration.comms.mocked", run_id=run.id, summary=response.get("summary"))
            return response

        raise ValueError(f"Unsupported workflow state: {state}")

    def _step_input_payload(self, state: WorkflowState, payload: dict[str, object]) -> dict[str, object]:
        if state == "fetch_hierarchy":
            return {}
        if not payload:
            return {}
        last_key = list(payload)[-1]
        previous = payload.get(last_key)
        return previous if isinstance(previous, dict) else {}
