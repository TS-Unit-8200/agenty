"""Phone tool support for council-agent tool use during the agent session."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from agenty.mcp import MCPProvider
from agenty.mcp_gateway.base import MCPGateway
from agenty.orchestration.exceptions import AgentToolPause
from agenty.orchestration.models import ExternalInfoRequest
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.tracing import trace_event

PHONE_TOOL_NAME = "phone_query_resource"
PHONE_TOOL_ELIGIBLE_AGENT_IDS = {
    "dyrektor-abw",
    "dyrektor-szpitala",
    "komendant-psp",
    "komendant-policji",
    "logistyk",
}

_AGENT_HINTS: dict[str, tuple[str, ...]] = {
    "dyrektor-abw": ("operator", "zaklad", "plant", "scada", "cyber", "it"),
    "dyrektor-szpitala": ("hospital", "szpital", "medical", "wsrm", "dyspozytor"),
    "komendant-psp": ("fire", "psp", "rescue", "ratown", "stanowisko", "kierowania"),
    "komendant-policji": ("police", "policja", "ruch", "objazd", "kpp", "dyzurny", "s17", "dk17", "wezel"),
    "logistyk": ("fuel", "logistics", "transport", "paliwo", "logistyka"),
}

_CONTACT_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "operator": ("operator", "zaklad", "plant", "scada", "cyber", "it"),
    "plant": ("operator", "zaklad", "plant"),
    "hospital": ("hospital", "szpital", "medical"),
    "medical": ("hospital", "szpital", "medical"),
    "fire": ("fire", "psp", "ratown"),
    "police": ("police", "policja", "ruch"),
    "road_operator": ("road", "road_operator", "gddkia", "zarzadca", "drogi", "s17", "objazd", "wezel"),
    "road": ("road", "road_operator", "gddkia", "zarzadca", "drogi", "s17", "objazd", "wezel"),
    "traffic": ("road", "road_operator", "gddkia", "zarzadca", "drogi", "s17", "objazd", "wezel", "ruch"),
    "medical_dispatch": ("medical_dispatch", "medical", "szpital", "hospital", "wsrm", "dyspozytor"),
    "logistics": ("logistics", "transport", "fuel", "paliwo", "logistyka"),
    "wios": ("wios", "gis", "sanepid", "environment", "chem", "lab"),
    "gis": ("wios", "gis", "sanepid", "environment", "chem", "lab"),
}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _ensure_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _dedupe_unknowns(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        key = _normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _score_resource(
    resource: dict[str, Any],
    *,
    preferred_contact_type: str | None,
    unknowns: list[str],
    agent_id: str,
) -> int:
    haystack = " ".join(
        part
        for part in (
            _normalize_text(resource.get("type")),
            _normalize_text(resource.get("contact_role")),
            _normalize_text(resource.get("name")),
        )
        if part
    )
    if not haystack:
        return 0

    score = 0
    preferred_key = _normalize_text(preferred_contact_type)
    if preferred_key:
        for token in _CONTACT_TYPE_HINTS.get(preferred_key, (preferred_key,)):
            if token in haystack:
                score += 4

    for token in _AGENT_HINTS.get(agent_id, ()):
        if token in haystack:
            score += 2

    unknowns_joined = " ".join(_normalize_text(item) for item in unknowns)
    for token in unknowns_joined.split():
        if len(token) >= 4 and token in haystack:
            score += 1

    return score


def _pick_best_resource(
    resources: list[dict[str, Any]],
    *,
    preferred_contact_type: str | None,
    unknowns: list[str],
    agent_id: str,
) -> dict[str, Any] | None:
    candidates = [
        dict(item)
        for item in resources
        if isinstance(item, dict) and str(item.get("contact_phone", "")).strip()
    ]
    if not candidates:
        return None
    ranked = [
        (
            _score_resource(
                item,
                preferred_contact_type=preferred_contact_type,
                unknowns=unknowns,
                agent_id=agent_id,
            ),
            item,
        )
        for item in candidates
    ]
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best_item = ranked[0]
    if best_score > 0:
        return best_item

    unique_phones = {
        str(item.get("contact_phone", "")).strip()
        for item in candidates
        if str(item.get("contact_phone", "")).strip()
    }
    if len(candidates) > 1 and len(unique_phones) == 1:
        return best_item
    return None


def _build_schema(unknowns: list[str]) -> dict[str, Any]:
    chosen = unknowns[:5] or ["Potwierdz aktualna dostepnosc i ograniczenia operacyjne."]
    properties = {
        f"answer_{index + 1}": {"type": "string", "description": item}
        for index, item in enumerate(chosen)
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
    }


def _build_requirements(resource: dict[str, Any], *, why: str | None, unknowns: list[str]) -> str:
    gap_lines = "\n".join(f"- {item}" for item in unknowns[:5]) or "- Potwierdz najwazniejsze liczby operacyjne."
    why_line = f"Powod: {why.strip()}\n" if str(why or "").strip() else ""
    return (
        f"Skontaktuj sie z zasobem '{resource.get('name', 'zasob')}' i uzupelnij brakujace dane do analizy kryzysowej.\n"
        f"{why_line}"
        "Zbierz tylko informacje, ktorych agent teraz nie zna i ktore mozna potwierdzic telefonicznie.\n"
        "Luki do uzupelnienia:\n"
        f"{gap_lines}\n"
        "Dopytaj o liczby, ETA, ograniczenia i aktualna dostepnosc."
    )


def _request_notice(request: ExternalInfoRequest, *, completed: bool = False) -> str:
    if completed:
        return f"Rozmowa z {request.resource_name} zakonczona, agent przygotowuje finalna odpowiedz."
    return f"Trwa rozmowa z {request.resource_name}."


def _tool_payload_from_request(request: ExternalInfoRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": request.status,
        "budget_status": request.budget_status,
        "resource_id": request.resource_id,
        "resource_name": request.resource_name,
        "contact_role": request.contact_role,
        "unknowns": request.unknowns,
        "reason": request.reason,
        "notice": request.notice,
    }
    if request.result is not None:
        payload["result"] = request.result
    if request.error:
        payload["error"] = request.error
    if request.transcript_excerpt:
        payload["transcript_excerpt"] = request.transcript_excerpt
    return payload


def _normalize_external_status(value: Any) -> str:
    status = _normalize_text(value)
    if status in {"completed", "failed", "timed_out", "timeout", "waiting", "initiated"}:
        return "timed_out" if status == "timeout" else status
    return "waiting"


class CouncilPhoneToolProvider(MCPProvider):
    def __init__(
        self,
        *,
        repository: OrchestrationRepository,
        mcp: MCPGateway,
        run_id: str,
        incident_id: str,
        agent_id: str,
        phone_poll_interval_s: float,
        execution_mode: str | None = None,
    ) -> None:
        self._repository = repository
        self._mcp = mcp
        self._run_id = run_id
        self._incident_id = incident_id
        self._agent_id = agent_id
        self._phone_poll_interval_s = phone_poll_interval_s
        self._execution_mode = execution_mode

    def list_tool_specs(self) -> list[dict[str, Any]]:
        if self._agent_id not in PHONE_TOOL_ELIGIBLE_AGENT_IDS:
            return []
        return [
            {
                "name": PHONE_TOOL_NAME,
                "description": (
                    "Wykonaj jedno telefoniczne potwierdzenie brakujacych danych na podstawie zasobow incydentu. "
                    "Uzyj tylko wtedy, gdy bez rozmowy nie da sie potwierdzic kluczowych faktow."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "unknowns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista konkretnych brakow informacyjnych do potwierdzenia.",
                        },
                        "preferred_contact_type": {
                            "type": "string",
                            "description": "Preferowany typ kontaktu, np. operator, hospital, fire, police, logistics, wios.",
                        },
                        "why": {
                            "type": "string",
                            "description": "Jednozdaniowe uzasadnienie, dlaczego potrzebne jest to potwierdzenie.",
                        },
                    },
                    "required": ["unknowns", "preferred_contact_type", "why"],
                },
            }
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name != PHONE_TOOL_NAME:
            raise KeyError(f"Unsupported tool: {name}")

        existing = self._repository.get_active_external_info_request(self._run_id)
        if existing is not None:
            trace_event(
                "agent.phone_tool.denied",
                run_id=self._run_id,
                agent_id=self._agent_id,
                resource_id=existing.resource_id,
                reason="call_in_progress",
            )
            return json.dumps(
                {
                    "status": "denied_budget_exhausted",
                    "resource_id": existing.resource_id,
                    "resource_name": existing.resource_name,
                    "reason": "W tym runie trwa juz inne polaczenie telefoniczne. Sprobuj ponownie po jego zakonczeniu.",
                    "notice": "W tym runie trwa juz inne polaczenie telefoniczne. Sprobuj ponownie po jego zakonczeniu.",
                },
                ensure_ascii=False,
            )

        unknowns = _dedupe_unknowns(list(arguments.get("unknowns", [])))
        preferred_contact_type = str(arguments.get("preferred_contact_type") or "").strip() or None
        why = str(arguments.get("why") or "").strip() or None

        resources_raw = self._mcp.call_tool("resource_list", {"incident_id": self._incident_id})
        resources = json.loads(resources_raw)
        resource = _pick_best_resource(
            resources if isinstance(resources, list) else [],
            preferred_contact_type=preferred_contact_type,
            unknowns=unknowns,
            agent_id=self._agent_id,
        )
        if resource is None:
            trace_event(
                "agent.phone_tool.unavailable",
                run_id=self._run_id,
                agent_id=self._agent_id,
                reason="no_contact_resource",
            )
            return json.dumps(
                {
                    "status": "unavailable_no_contact",
                    "reason": "Incydent nie ma przypisanego zasobu z numerem kontaktowym pasujacego do tej luki.",
                    "notice": "Brak przypisanego kontaktu dla tej luki. Agent nie moze potwierdzic danych telefonicznie.",
                    "preferred_contact_type": preferred_contact_type,
                    "unknowns": unknowns,
                },
                ensure_ascii=False,
            )

        request = ExternalInfoRequest(
            id=f"ext_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
            run_id=self._run_id,
            incident_id=self._incident_id,
            resource_id=str(resource.get("resource_id")),
            resource_name=str(resource.get("name", "Zasob")),
            phone_number=str(resource.get("contact_phone")),
            resource_type=str(resource.get("type") or "") or None,
            contact_name=str(resource.get("contact_name") or "") or None,
            contact_role=str(resource.get("contact_role") or "") or None,
            owner_agent_id=self._agent_id,
            preferred_contact_type=preferred_contact_type,
            unknowns=unknowns,
            schema_def=_build_schema(unknowns),
            requirements=_build_requirements(resource, why=why, unknowns=unknowns),
            reason=why or "Agent potrzebuje telefonicznego potwierdzenia danych.",
            status="initiated",
            budget_status="reserved",
            notice=f"Agent {self._agent_id} rozpoczyna rozmowe z {resource.get('name', 'zasob')}.",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        start_raw = self._mcp.call_tool(
            "phone_agent_start_call",
            {
                "phone_number": request.phone_number,
                "schema": request.schema_def,
                "requirements": request.requirements,
                "resource_name": request.resource_name,
                "execution_mode": self._execution_mode or "default",
            },
        )
        start_payload = json.loads(start_raw)
        call_id = str(start_payload.get("call_id") or "").strip()
        if not call_id:
            request.status = "failed"
            request.budget_status = "failed"
            request.notice = f"Nie udalo sie rozpoczac rozmowy z {request.resource_name}."
            request.error = str(start_payload.get("detail") or start_payload.get("error") or "missing call_id")
            request.updated_at = datetime.now(UTC)
            request.completed_at = datetime.now(UTC)
            self._repository.upsert_external_info_request(request)
            trace_event(
                "agent.phone_tool.start_failed",
                run_id=self._run_id,
                agent_id=self._agent_id,
                error=request.error,
            )
            return json.dumps(_tool_payload_from_request(request), ensure_ascii=False)

        request.call_id = call_id
        request.status = "waiting"
        request.notice = _request_notice(request)
        request.updated_at = datetime.now(UTC)
        self._repository.upsert_external_info_request(request)
        trace_event(
            "agent.phone_tool.started",
            run_id=self._run_id,
            agent_id=self._agent_id,
            call_id=call_id,
            resource_id=request.resource_id,
        )
        raise AgentToolPause(request=request, delay_s=self._phone_poll_interval_s)


def poll_external_info_request(
    *,
    repository: OrchestrationRepository,
    mcp: MCPGateway,
    request: ExternalInfoRequest,
    phone_max_wait_s: float,
) -> tuple[ExternalInfoRequest, str | None]:
    if request.status in {"completed", "failed", "timed_out", "skipped"}:
        return request, json.dumps(_tool_payload_from_request(request), ensure_ascii=False)

    elapsed_s = (datetime.now(UTC) - _ensure_utc(request.created_at)).total_seconds()
    if elapsed_s >= phone_max_wait_s:
        request.status = "timed_out"
        request.budget_status = "timed_out"
        request.notice = f"Rozmowa z {request.resource_name} przekroczyla limit oczekiwania."
        request.error = request.notice
        request.updated_at = datetime.now(UTC)
        request.completed_at = datetime.now(UTC)
        repository.upsert_external_info_request(request)
        trace_event("agent.phone_tool.timed_out", run_id=request.run_id, agent_id=request.owner_agent_id, call_id=request.call_id)
        return request, json.dumps(_tool_payload_from_request(request), ensure_ascii=False)

    poll_raw = mcp.call_tool("phone_agent_get_call", {"call_id": request.call_id})
    poll_payload = json.loads(poll_raw)
    external_status = _normalize_external_status(poll_payload.get("status"))

    if external_status == "completed":
        transcript_lines: list[str] = []
        transcript = poll_payload.get("transcript")
        if isinstance(transcript, list):
            for item in transcript[-3:]:
                if isinstance(item, dict) and str(item.get("text", "")).strip():
                    transcript_lines.append(f"{item.get('role', 'speaker')}: {str(item.get('text', '')).strip()}")
        request.status = "completed"
        request.budget_status = "completed"
        request.result = poll_payload.get("result") if isinstance(poll_payload.get("result"), dict) else None
        request.transcript_excerpt = "\n".join(transcript_lines) or None
        request.notice = _request_notice(request, completed=True)
        request.error = None
        request.updated_at = datetime.now(UTC)
        request.completed_at = datetime.now(UTC)
        repository.upsert_external_info_request(request)
        if request.result:
            repository.append_incident_update(
                request.incident_id,
                author_role=request.owner_agent_id or "orchestrator",
                content=f"Telefoniczne potwierdzenie z {request.resource_name}: " + json.dumps(request.result, ensure_ascii=False),
                update_type="phone_verification",
            )
        trace_event("agent.phone_tool.completed", run_id=request.run_id, agent_id=request.owner_agent_id, call_id=request.call_id)
        return request, json.dumps(_tool_payload_from_request(request), ensure_ascii=False)

    if external_status == "failed":
        request.status = "failed"
        request.budget_status = "failed"
        request.notice = f"Rozmowa z {request.resource_name} zakonczyla sie bledem."
        request.error = str(poll_payload.get("detail") or poll_payload.get("error") or "call failed")
        request.updated_at = datetime.now(UTC)
        request.completed_at = datetime.now(UTC)
        repository.upsert_external_info_request(request)
        trace_event("agent.phone_tool.failed", run_id=request.run_id, agent_id=request.owner_agent_id, call_id=request.call_id, error=request.error)
        return request, json.dumps(_tool_payload_from_request(request), ensure_ascii=False)

    request.status = "waiting"
    request.budget_status = "reserved"
    request.notice = _request_notice(request)
    request.updated_at = datetime.now(UTC)
    repository.upsert_external_info_request(request)
    return request, None
