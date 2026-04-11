"""Mongo persistence for orchestration workflow state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agenty.db.mongo import MongoConnector
from agenty.orchestration.models import AgentRun, ScenarioVersion, WorkflowRun, WorkflowState, WorkflowStep


class OrchestrationRepository:
    def __init__(self, connector: MongoConnector) -> None:
        self._db = connector.get_database()
        self._runs = self._db["workflow_runs"]
        self._steps = self._db["workflow_steps"]
        self._agent_runs = self._db["agent_runs"]
        self._scenario_versions = self._db["scenario_versions"]
        self._organizations = self._db["organizations"]
        self._runs.create_index([("id", 1)], unique=True)
        self._runs.create_index([("incident_id", 1), ("orchestrator_version", 1)])
        self._steps.create_index([("run_id", 1), ("state", 1)], unique=True)
        self._agent_runs.create_index([("run_id", 1)])
        self._scenario_versions.create_index([("id", 1)], unique=True)

    @staticmethod
    def _strip_id(doc: dict[str, Any] | None) -> dict[str, Any] | None:
        if doc is None:
            return None
        return {key: value for key, value in doc.items() if key != "_id"}

    def find_existing_run(self, incident_id: str, orchestrator_version: str) -> WorkflowRun | None:
        doc = self._runs.find_one(
            {
                "incident_id": incident_id,
                "orchestrator_version": orchestrator_version,
                "status": {"$nin": ["failed"]},
            },
            {"_id": 0},
        )
        clean = self._strip_id(doc)
        return WorkflowRun.model_validate(clean) if clean else None

    def create_run(self, run: WorkflowRun) -> WorkflowRun:
        self._runs.insert_one(run.model_dump())
        return run

    def get_run(self, run_id: str) -> WorkflowRun | None:
        doc = self._runs.find_one({"id": run_id}, {"_id": 0})
        clean = self._strip_id(doc)
        return WorkflowRun.model_validate(clean) if clean else None

    def update_run_state(
        self,
        run_id: str,
        *,
        status: WorkflowState,
        current_state: WorkflowState,
        last_error: str | None = None,
        completed: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        set_payload: dict[str, Any] = {
            "status": status,
            "current_state": current_state,
            "updated_at": now,
            "last_error": last_error,
        }
        if completed:
            set_payload["completed_at"] = now
        self._runs.update_one({"id": run_id}, {"$set": set_payload})

    def upsert_step(self, step: WorkflowStep) -> WorkflowStep:
        self._steps.update_one(
            {"run_id": step.run_id, "state": step.state},
            {"$set": step.model_dump()},
            upsert=True,
        )
        return step

    def list_steps(self, run_id: str) -> list[WorkflowStep]:
        return [
            WorkflowStep.model_validate(self._strip_id(doc))
            for doc in self._steps.find({"run_id": run_id}, {"_id": 0}).sort("started_at", 1)
        ]

    def append_agent_run(self, agent_run: AgentRun) -> AgentRun:
        self._agent_runs.insert_one(agent_run.model_dump())
        return agent_run

    def list_agent_runs(self, run_id: str) -> list[AgentRun]:
        return [
            AgentRun.model_validate(self._strip_id(doc))
            for doc in self._agent_runs.find({"run_id": run_id}, {"_id": 0}).sort("started_at", 1)
        ]

    def save_scenario_version(self, version: ScenarioVersion) -> ScenarioVersion:
        self._scenario_versions.insert_one(version.model_dump())
        return version

    def get_scenario_version(self, version_id: str) -> ScenarioVersion | None:
        doc = self._scenario_versions.find_one({"id": version_id}, {"_id": 0})
        clean = self._strip_id(doc)
        return ScenarioVersion.model_validate(clean) if clean else None

    def update_incident_links(
        self,
        incident_id: str,
        *,
        run_id: str,
        scenario_version_id: str | None,
    ) -> None:
        set_payload: dict[str, Any] = {
            "incidents.$.latest_orchestration_run_id": run_id,
        }
        if scenario_version_id:
            set_payload["incidents.$.latest_scenario_version_id"] = scenario_version_id
        self._organizations.update_many(
            {"incidents.id": incident_id},
            {"$set": set_payload},
        )

    def find_org_hierarchy_for_incident(self, incident_id: str) -> dict[str, Any] | None:
        doc = self._organizations.find_one(
            {"incidents.id": incident_id},
            {
                "_id": 0,
                "id": 1,
                "external_id": 1,
                "slug": 1,
                "name": 1,
                "hierarchy": 1,
                "incidents": {"$elemMatch": {"id": incident_id}},
            },
        )
        return doc
