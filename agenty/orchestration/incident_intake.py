"""Map UI / citizen incident reports into Mongo organization incidents + LLM enrichment."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from agenty.agent import AgentRuntime

logger = logging.getLogger(__name__)

# Default map hints when the UI does not send coordinates (aligned with frontend powiat list).
_POWIAT_DEFAULTS: dict[str, tuple[float, float, str]] = {
    "lubelski": (51.2465, 22.5684, "Lublin"),
    "świdnicki": (51.1321, 22.8456, "Piaski"),
    "zamojski": (50.7231, 23.2519, "Zamość"),
    "opolski": (51.3987, 22.1234, "Opole Lubelskie"),
    "chełmski": (51.143, 23.4721, "Chełm"),
    "puławski": (51.4167, 21.9696, "Puławy"),
    "kraśnicki": (50.923, 22.221, "Kraśnik"),
}


class IntakeLlmFields(BaseModel):
    """Structured fields produced by the LLM from the raw report."""

    affected_population: int = Field(default=0, ge=0)
    voivodeship: str = Field(default="lubelskie", max_length=80)
    narrative_summary: str = Field(default="", max_length=4000)


class NarrativeIncidentDraft(BaseModel):
    """Full incident fields inferred by the LLM from free text (operator narrative)."""

    title: str = Field(min_length=3, max_length=200)
    type: str = Field(min_length=1, max_length=64)
    priority: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=10, max_length=20_000)
    powiat: str = Field(min_length=2, max_length=80)
    gmina: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=3, max_length=500)
    affected_population: int = Field(default=0, ge=0)
    voivodeship: str = Field(default="lubelskie", max_length=80)


_ALLOWED_TYPES = frozenset(
    {"blackout", "flood", "chemical", "accident", "infrastructure", "cyber", "other"},
)
_ALLOWED_PRIORITY = frozenset({"critical", "high", "medium", "low"})


def _resolve_coords(powiat: str, gmina: str | None, lat: float | None, lng: float | None) -> tuple[float, float, str]:
    if lat is not None and lng is not None:
        g = (gmina or "").strip() or powiat
        return lat, lng, g
    key = powiat.strip().lower()
    lat2, lng2, gmina_default = _POWIAT_DEFAULTS.get(key, _POWIAT_DEFAULTS["lubelski"])
    g = (gmina or "").strip() or gmina_default
    return lat2, lng2, g


def _parse_llm_json_object(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw).strip()
    return json.loads(raw)


def draft_incident_from_narrative_llm(
    runtime: AgentRuntime,
    narrative: str,
    *,
    lat_hint: float | None,
    lng_hint: float | None,
) -> NarrativeIncidentDraft:
    """Infer title, type, priority, geography, and description from unstructured Polish text."""
    geo = ""
    if lat_hint is not None and lng_hint is not None:
        geo = f"\n\nWskazówka geograficzna (współrzędne z mapy): lat={lat_hint}, lng={lng_hint}."
    instruction = (
        "Jesteś analitykiem sytuacji kryzysowych. Na podstawie NARRACJI OPERATORA zwróć WYŁĄCZNIE jeden obiekt JSON "
        "(bez markdown) z kluczami dokładnie:\n"
        "title (krótki tytuł incydentu po polsku),\n"
        "type — jedna z: blackout, flood, chemical, accident, infrastructure, cyber, other,\n"
        "priority — jedna z: critical, high, medium, low,\n"
        "description — pełny opis operacyjny po polsku (2–12 zdań),\n"
        "powiat — nazwa powiatu małymi literami ASCII (np. lubelski, polkowicki),\n"
        "gmina — nazwa gminy/miejscowości,\n"
        "address — jedna linia adresu lub opis lokalizacji,\n"
        "affected_population — liczba całkowita >= 0 (szacunek),\n"
        "voivodeship — województwo po polsku, krótko.\n\n"
        f"NARRACJA:\n{narrative.strip()}{geo}"
    )
    messages = [{"role": "user", "content": instruction}]
    raw = ""
    try:
        raw = runtime.llm.chat_completion(
            messages,
            response_format={"type": "json_object"},
            log_label="intake:narrative_draft_json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Narrative intake: json_object failed (%s); plain completion", exc)
        raw = runtime.llm.chat_completion(messages, log_label="intake:narrative_draft_plain")
    try:
        data = _parse_llm_json_object(raw)
        draft = NarrativeIncidentDraft.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Narrative intake LLM failed (%s); using minimal fallback", exc)
        draft = NarrativeIncidentDraft(
            title="Zgłoszenie — wymaga weryfikacji",
            type="other",
            priority="high",
            description=narrative.strip()[:8000],
            powiat="lubelski",
            gmina="nieznana",
            address="Lokalizacja do uzupełnienia",
            affected_population=0,
            voivodeship="lubelskie",
        )
    t = draft.type.strip().lower()
    if t not in _ALLOWED_TYPES:
        draft = draft.model_copy(update={"type": "other"})
    p = draft.priority.strip().lower()
    if p not in _ALLOWED_PRIORITY:
        draft = draft.model_copy(update={"priority": "high"})
    return draft


def enrich_report_with_llm(runtime: AgentRuntime, payload: dict[str, Any]) -> IntakeLlmFields:
    """Ask the model for population estimate, voivodeship, and a concise operational summary."""
    instruction = (
        "Jesteś analizatorem zgłoszeń kryzysowych. Na podstawie pól JSON wejściowych zwróć "
        "WYŁĄCZNIE jeden obiekt JSON (bez markdown, bez komentarzy) o kluczach dokładnie: "
        "affected_population (liczba całkowita >= 0, szacunek dotkniętych osób), "
        "voivodeship (nazwa województwa w Polsce, krótko), "
        "narrative_summary (2–4 zdania po polsku: synteza sytuacji dla operatorów).\n\n"
        f"Wejście:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    messages = [{"role": "user", "content": instruction}]
    raw = ""
    try:
        raw = runtime.llm.chat_completion(
            messages,
            response_format={"type": "json_object"},
            log_label="intake:enrich_report_json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("LLM json_object unsupported or failed (%s); retrying plain completion", exc)
        raw = runtime.llm.chat_completion(messages, log_label="intake:enrich_report_plain")
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Intake LLM returned non-JSON; using defaults. Raw snippet: %s", raw[:500])
        return IntakeLlmFields()
    try:
        return IntakeLlmFields.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Intake LLM JSON parse failed (%s); using defaults", exc)
        return IntakeLlmFields()


def build_mongo_incident_document(
    *,
    incident_id: str,
    title: str,
    description: str,
    incident_type: str,
    priority: str,
    powiat: str,
    gmina: str,
    lat: float,
    lng: float,
    address: str,
    voivodeship: str,
    affected_population: int,
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    ext = f"inc_report_{incident_id[:12]}"
    return {
        "id": incident_id,
        "external_id": ext,
        "title": title.strip(),
        "type": incident_type,
        "priority": priority,
        "status": "new",
        "description": description.strip(),
        "affected_population": affected_population,
        "assigned_to": "operator",
        "created_by": "citizen_report",
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
        "map_metadata": {
            "location": {
                "lat": lat,
                "lng": lng,
                "geojson": {"type": "Point", "coordinates": [lng, lat]},
            },
            "address": address,
            "powiat": powiat,
            "gmina": gmina,
            "voivodeship": voivodeship,
        },
        "updates": [],
        "resources": [],
        "scenarios": [],
    }


def new_incident_id() -> str:
    """24-char hex id similar to Payload/Mongo style ids used in seed data."""
    return uuid.uuid4().hex[:24]
