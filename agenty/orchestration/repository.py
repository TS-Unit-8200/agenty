"""Mongo persistence for orchestration workflow state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agenty.db.mongo import MongoConnector
from agenty.orchestration.models import (
    AgentRun,
    AgentToolSession,
    ExternalInfoRequest,
    ScenarioVersion,
    WorkflowRun,
    WorkflowState,
    WorkflowStep,
)
from agenty.orchestration.state_machine import TERMINAL_STATES, WORKFLOW_PATH


class OrchestrationRepository:
    def __init__(self, connector: MongoConnector) -> None:
        self._db = connector.get_database()
        self._runs = self._db["workflow_runs"]
        self._steps = self._db["workflow_steps"]
        self._agent_runs = self._db["agent_runs"]
        self._agent_tool_sessions = self._db["agent_tool_sessions"]
        self._scenario_versions = self._db["scenario_versions"]
        self._external_info_requests = self._db["external_info_requests"]
        self._organizations = self._db["organizations"]
        self._runs.create_index([("id", 1)], unique=True)
        self._runs.create_index([("incident_id", 1), ("orchestrator_version", 1), ("execution_mode", 1)])
        self._runs.create_index([("incident_id", 1), ("updated_at", -1)])
        self._steps.create_index([("run_id", 1), ("state", 1)], unique=True)
        self._agent_runs.create_index([("run_id", 1)])
        self._agent_tool_sessions.create_index([("run_id", 1), ("agent_id", 1)], unique=True)
        self._scenario_versions.create_index([("id", 1)], unique=True)
        for index_name, info in self._external_info_requests.index_information().items():
            if info.get("unique") and info.get("key") == [("run_id", 1)]:
                self._external_info_requests.drop_index(index_name)
        self._external_info_requests.create_index([("id", 1)], unique=True)
        self._external_info_requests.create_index([("run_id", 1), ("updated_at", -1)])
        self._external_info_requests.create_index([("run_id", 1), ("status", 1), ("updated_at", -1)])
        self._external_info_requests.create_index([("status", 1), ("updated_at", -1)])

    @staticmethod
    def _strip_id(doc: dict[str, Any] | None) -> dict[str, Any] | None:
        if doc is None:
            return None
        return {key: value for key, value in doc.items() if key != "_id"}

    @staticmethod
    def _workflow_progress_rank(state: str | None) -> int:
        if not state:
            return -1
        if state == "comms_mock_call":
            return len(WORKFLOW_PATH)
        try:
            return WORKFLOW_PATH.index(state)  # type: ignore[arg-type]
        except ValueError:
            return -1

    def find_latest_active_run(
        self,
        incident_id: str,
        orchestrator_version: str,
        execution_mode: str = "default",
    ) -> WorkflowRun | None:
        execution_mode_query: dict[str, Any]
        if execution_mode == "default":
            execution_mode_query = {"$or": [{"execution_mode": "default"}, {"execution_mode": {"$exists": False}}]}
        else:
            execution_mode_query = {"execution_mode": execution_mode}
        docs = list(
            self._runs.find(
                {
                    "incident_id": incident_id,
                    "orchestrator_version": orchestrator_version,
                    "status": {"$nin": list(TERMINAL_STATES)},
                    **execution_mode_query,
                },
                {"_id": 0},
            )
        )
        if not docs:
            return None
        docs.sort(
            key=lambda doc: (
                self._workflow_progress_rank(doc.get("current_state") or doc.get("status")),
                doc.get("updated_at") or doc.get("started_at"),
            ),
            reverse=True,
        )
        clean = self._strip_id(docs[0])
        return WorkflowRun.model_validate(clean) if clean else None

    def list_runs(
        self,
        *,
        incident_id: str | None = None,
        orchestrator_version: str | None = None,
        execution_mode: str | None = None,
    ) -> list[WorkflowRun]:
        query: dict[str, Any] = {}
        if incident_id is not None:
            query["incident_id"] = incident_id
        if orchestrator_version is not None:
            query["orchestrator_version"] = orchestrator_version
        if execution_mode is not None:
            query["execution_mode"] = execution_mode
        return [
            WorkflowRun.model_validate(self._strip_id(doc))
            for doc in self._runs.find(query, {"_id": 0}).sort("updated_at", -1)
        ]

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

    def touch_run(self, run_id: str, *, current_state: WorkflowState | None = None) -> None:
        payload: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if current_state is not None:
            payload["current_state"] = current_state
        self._runs.update_one({"id": run_id}, {"$set": payload})

    def upsert_step(self, step: WorkflowStep) -> WorkflowStep:
        self._steps.update_one(
            {"run_id": step.run_id, "state": step.state},
            {"$set": step.model_dump()},
            upsert=True,
        )
        return step

    def get_step(self, run_id: str, state: WorkflowState) -> WorkflowStep | None:
        doc = self._steps.find_one({"run_id": run_id, "state": state}, {"_id": 0})
        clean = self._strip_id(doc)
        return WorkflowStep.model_validate(clean) if clean else None

    def touch_step(self, run_id: str, state: WorkflowState) -> None:
        self._steps.update_one(
            {"run_id": run_id, "state": state},
            {"$set": {"updated_at": datetime.now(UTC)}},
        )

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

    def upsert_agent_tool_session(self, session: AgentToolSession) -> AgentToolSession:
        self._agent_tool_sessions.update_one(
            {"run_id": session.run_id, "agent_id": session.agent_id},
            {"$set": session.model_dump(mode="python")},
            upsert=True,
        )
        return session

    def get_agent_tool_session(self, run_id: str, agent_id: str) -> AgentToolSession | None:
        doc = self._agent_tool_sessions.find_one({"run_id": run_id, "agent_id": agent_id}, {"_id": 0})
        clean = self._strip_id(doc)
        return AgentToolSession.model_validate(clean) if clean else None

    def list_agent_tool_sessions(self, run_id: str) -> list[AgentToolSession]:
        return [
            AgentToolSession.model_validate(self._strip_id(doc))
            for doc in self._agent_tool_sessions.find({"run_id": run_id}, {"_id": 0}).sort("created_at", 1)
        ]

    def delete_agent_tool_session(self, run_id: str, agent_id: str) -> None:
        self._agent_tool_sessions.delete_one({"run_id": run_id, "agent_id": agent_id})

    def save_scenario_version(self, version: ScenarioVersion) -> ScenarioVersion:
        self._scenario_versions.insert_one(version.model_dump())
        return version

    def get_scenario_version(self, version_id: str) -> ScenarioVersion | None:
        doc = self._scenario_versions.find_one({"id": version_id}, {"_id": 0})
        clean = self._strip_id(doc)
        return ScenarioVersion.model_validate(clean) if clean else None

    def upsert_external_info_request(self, request: ExternalInfoRequest) -> ExternalInfoRequest:
        self._external_info_requests.update_one(
            {"id": request.id},
            {"$set": request.model_dump(mode="python")},
            upsert=True,
        )
        return request

    def get_external_info_request(self, run_id: str) -> ExternalInfoRequest | None:
        doc = self._external_info_requests.find_one(
            {"run_id": run_id},
            {"_id": 0},
            sort=[("updated_at", -1), ("created_at", -1)],
        )
        clean = self._strip_id(doc)
        return ExternalInfoRequest.model_validate(clean) if clean else None

    def get_active_external_info_request(self, run_id: str) -> ExternalInfoRequest | None:
        doc = self._external_info_requests.find_one(
            {"run_id": run_id, "status": {"$in": ["initiated", "waiting"]}},
            {"_id": 0},
            sort=[("updated_at", -1), ("created_at", -1)],
        )
        clean = self._strip_id(doc)
        return ExternalInfoRequest.model_validate(clean) if clean else None

    def list_external_info_requests(
        self,
        *,
        statuses: list[str] | None = None,
    ) -> list[ExternalInfoRequest]:
        query: dict[str, Any] = {}
        if statuses:
            query["status"] = {"$in": statuses}
        return [
            ExternalInfoRequest.model_validate(self._strip_id(doc))
            for doc in self._external_info_requests.find(query, {"_id": 0}).sort("updated_at", -1)
        ]

    def update_incident_links(
        self,
        incident_id: str,
        *,
        run_id: str,
        scenario_version_id: str | None,
    ) -> None:
        set_payload: dict[str, Any] = {
            "incidents.$[elem].latest_orchestration_run_id": run_id,
        }
        if scenario_version_id:
            set_payload["incidents.$[elem].latest_scenario_version_id"] = scenario_version_id
        for filter_key, array_filter in (
            ("id", {"elem.id": incident_id}),
            ("external_id", {"elem.external_id": incident_id}),
        ):
            filt: dict[str, Any] = {f"incidents.{filter_key}": incident_id}
            self._organizations.update_many(filt, {"$set": set_payload}, array_filters=[array_filter])

    def append_incident_update(
        self,
        incident_id: str,
        *,
        author_role: str,
        content: str,
        update_type: str,
    ) -> None:
        now = datetime.now(UTC)
        update_doc = {
            "external_id": f"upd_{uuid4().hex[:12]}",
            "author_role": author_role,
            "content": content,
            "type": update_type,
            "created_at": now,
        }
        for filter_key, array_filter in (
            ("id", {"elem.id": incident_id}),
            ("external_id", {"elem.external_id": incident_id}),
        ):
            filt: dict[str, Any] = {f"incidents.{filter_key}": incident_id}
            self._organizations.update_many(
                filt,
                {
                    "$push": {"incidents.$[elem].updates": update_doc},
                    "$set": {"incidents.$[elem].updated_at": now},
                },
                array_filters=[array_filter],
            )

    def find_org_hierarchy_for_incident(self, incident_id: str) -> dict[str, Any] | None:
        elem = {"$elemMatch": {"$or": [{"id": incident_id}, {"external_id": incident_id}]}}
        doc = self._organizations.find_one(
            {"incidents": elem},
            {
                "_id": 0,
                "id": 1,
                "external_id": 1,
                "slug": 1,
                "name": 1,
                "hierarchy": 1,
                "incidents": elem,
            },
        )
        return doc

    def append_incident_to_organization(self, organization_external_id: str, incident: dict[str, Any]) -> None:
        """$push a new incident subdocument; ``incident`` must include a unique string ``id``."""
        result = self._organizations.update_one(
            {"external_id": organization_external_id},
            {"$push": {"incidents": incident}},
        )
        if result.matched_count == 0:
            raise KeyError(
                f"Organization with external_id={organization_external_id!r} not found. "
                "Seed Mongo or set INTAKE_DEFAULT_ORG_EXTERNAL_ID to match an existing org."
            )

    def mark_run_superseded(self, run_id: str, *, superseded_by: str) -> None:
        now = datetime.now(UTC)
        self._runs.update_one(
            {"id": run_id},
            {
                "$set": {
                    "status": "failed",
                    "current_state": "failed",
                    "updated_at": now,
                    "completed_at": now,
                    "last_error": f"superseded_by:{superseded_by}",
                }
            },
        )
