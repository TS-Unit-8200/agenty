"""Hierarchy and incident lookup helpers."""

from __future__ import annotations

from typing import Any

from agenty.orchestration.repository import OrchestrationRepository


class HierarchyService:
    def __init__(self, repository: OrchestrationRepository) -> None:
        self._repository = repository

    def load_context(self, incident_id: str) -> dict[str, Any]:
        doc = self._repository.find_org_hierarchy_for_incident(incident_id)
        if doc is None:
            raise KeyError(f"Incident {incident_id!r} not found in organizations collection")
        incident = (doc.get("incidents") or [{}])[0]
        return {
            "organization": {
                "id": doc.get("id"),
                "external_id": doc.get("external_id"),
                "slug": doc.get("slug"),
                "name": doc.get("name"),
            },
            "hierarchy": doc.get("hierarchy", {}),
            "incident": incident,
        }
