"""Selects participating agents from hierarchy and incident metadata."""

from __future__ import annotations

from typing import Any

MANDATORY_AGENTS = {
    "komendant-psp",
    "komendant-policji",
    "dyrektor-szpitala",
    "logistyk",
}

ROLE_TO_AGENT_ID = {
    "wojt": "wojt",
    "burmistrz": "wojt",
    "starosta": "starosta",
    "marszalek": "marszalek-wojewodztwa",
    "wojewoda": "wojewoda",
    "abw": "dyrektor-abw",
    "policji": "komendant-policji",
    "psp": "komendant-psp",
    "szpital": "dyrektor-szpitala",
    "logist": "logistyk",
}


def _walk_roles(node: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    role = str(node.get("role", "")).lower()
    if role:
        roles.append(role)
    for child in node.get("children", []):
        roles.extend(_walk_roles(child))
    return roles


class AgentSelector:
    def select(self, hierarchy: dict[str, Any], incident: dict[str, Any]) -> list[str]:
        selected = set(MANDATORY_AGENTS)
        for role in _walk_roles(hierarchy):
            for key, agent_id in ROLE_TO_AGENT_ID.items():
                if key in role:
                    selected.add(agent_id)

        incident_type = str(incident.get("type", "")).lower()
        description = str(incident.get("description", "")).lower()
        scope = str(incident.get("scope", "")).lower()

        if "powiat" in scope:
            selected.add("starosta")
        if "woj" in scope:
            selected.update({"marszalek-wojewodztwa", "wojewoda"})

        if any(token in incident_type for token in ("terror", "sabotage", "cyber")) or any(
            token in description for token in ("sabot", "terror", "cyber")
        ):
            selected.add("dyrektor-abw")

        return sorted(selected)
