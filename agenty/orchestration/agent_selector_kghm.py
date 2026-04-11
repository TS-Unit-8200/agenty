"""Agent selector for KGHM corporate crisis scenarios."""

from __future__ import annotations

from typing import Any

MANDATORY_AGENTS_KGHM = {
    "dyspozytor-ratownictwa",
    "szef-ochrony",
    "lekarz-zakladowy",
    "logistyk-kghm",
}

ROLE_TO_AGENT_ID_KGHM = {
    "kierownik-ruchu": "kierownik-ruchu-zakladu",
    "krzg": "kierownik-ruchu-zakladu",
    "sztygar": "kierownik-ruchu-zakladu",
    "dyrektor-zuz": "dyrektor-zuz",
    "dyrektor-zakladu": "dyrektor-zuz",
    "sztab": "dyrektor-zuz",
    "czlonek-zarzadu": "czlonek-zarzadu-operacje",
    "zarzad": "czlonek-zarzadu-operacje",
    "dyspozytor": "dyspozytor-ratownictwa",
    "ratownictwo": "dyspozytor-ratownictwa",
    "jrgh": "dyspozytor-ratownictwa",
    "ochrona": "szef-ochrony",
    "security": "szef-ochrony",
    "lekarz": "lekarz-zakladowy",
    "mcz": "lekarz-zakladowy",
    "medyczny": "lekarz-zakladowy",
    "logistyk": "logistyk-kghm",
    "utrzymanie": "logistyk-kghm",
    "csirt": "csirt-ot",
    "cyber": "csirt-ot",
    "scada": "csirt-ot",
    "rzecznik": "rzecznik-kryzysowy",
    "komunikacja": "rzecznik-kryzysowy",
    "media": "rzecznik-kryzysowy",
}


def _walk_roles(node: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    role = str(node.get("role", "")).lower()
    if role:
        roles.append(role)
    for child in node.get("children", []):
        roles.extend(_walk_roles(child))
    return roles


class AgentSelectorKGHM:
    """Select KGHM agents based on hierarchy and incident metadata."""

    def select(self, hierarchy: dict[str, Any], incident: dict[str, Any]) -> list[str]:
        selected = set(MANDATORY_AGENTS_KGHM)

        # Map roles from hierarchy tree
        for role in _walk_roles(hierarchy):
            for key, agent_id in ROLE_TO_AGENT_ID_KGHM.items():
                if key in role:
                    selected.add(agent_id)

        incident_type = str(incident.get("type", "")).lower()
        description = str(incident.get("description", "")).lower()
        scope = str(incident.get("scope", "")).lower()

        # Scope-based escalation
        if any(tok in scope for tok in ("zaklad", "kopalnia", "huta")):
            selected.update({"kierownik-ruchu-zakladu", "dyrektor-zuz"})
        if any(tok in scope for tok in ("grupa", "korporacja", "miedzyoddzial")):
            selected.update({"czlonek-zarzadu-operacje", "dyrektor-zuz"})

        # Incident-type specific agents
        cyber_tokens = ("cyber", "scada", "ransomware", "malware", "sabotaz", "hacking")
        if any(tok in incident_type for tok in cyber_tokens) or any(
            tok in description for tok in cyber_tokens
        ):
            selected.add("csirt-ot")

        # Public-facing incidents -> communications
        public_tokens = ("smierc", "ofiary", "media", "ewakuacja", "skazenie", "wyciek")
        if any(tok in incident_type for tok in public_tokens) or any(
            tok in description for tok in public_tokens
        ):
            selected.add("rzecznik-kryzysowy")

        # Always include orchestrator
        selected.add("orchestrator-kghm")
        return sorted(selected)
