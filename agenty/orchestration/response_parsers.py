"""Parsers for role-agent summaries and orchestrator scenario reports."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from uuid import uuid4

from agenty.db.models import Scenario, ScenarioActions
from agenty.orchestration.models import AgentRunSummary, ScenarioVersion

_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$")
_HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
_RECOMMENDATION_HEADING_RE = re.compile(
    r"(?ms)^##\s+Rekomendowany wariant\s*\n\s*(?:\*\*(?P<bold>.+?)\*\*|(?P<plain>.+?))\s*(?:\n|$)"
)
_SECTION_RE_TEMPLATE = r"(?ms)^{level}\s+{title}\s*\n(?P<body>.*?)(?=^##\s+|^###\s+|^#\s+|\Z)"


def _normalize_label(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.strip().lower())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _unique_keep_order(values: list[str], *, limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = _normalize_label(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
        if limit is not None and len(unique) >= limit:
            break
    return unique


def _collect_lines(text: str) -> list[str]:
    return [line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]


def _extract_bullets(text: str) -> list[str]:
    bullets: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        match = _BULLET_RE.match(line)
        if match:
            bullets.append(match.group(1).strip())
    return bullets


def _extract_section(markdown: str, heading_title: str, *, level: str = "###") -> str:
    escaped = re.escape(heading_title)
    pattern = re.compile(_SECTION_RE_TEMPLATE.format(level=re.escape(level), title=escaped))
    match = pattern.search(markdown)
    return match.group("body").strip() if match else ""


def _extract_subsection(markdown: str, heading_title: str) -> str:
    escaped = re.escape(heading_title)
    pattern = re.compile(
        rf"(?ms)^####\s+{escaped}\s*\n(?P<body>.*?)(?=^####\s+|^###\s+|^##\s+|^#\s+|\Z)"
    )
    match = pattern.search(markdown)
    return match.group("body").strip() if match else ""


def _first_paragraph(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    current: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current:
                break
            continue
        if _HEADING_RE.match(line) or line.startswith("|"):
            if current:
                break
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            if current:
                break
            current.append(bullet.group(1).strip())
            break
        current.append(line)
    paragraph = " ".join(current)
    return re.sub(r"\s+", " ", paragraph).strip()


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    return [part.strip() for part in parts if part.strip()]


def _detect_urgency(text: str) -> str:
    lower = text.lower()
    if re.search(r"\b(natychmiast|piln|asap|immediate|0-?2 ?h|2 ?h|4 ?h)\b", lower):
        return "immediate"
    if re.search(r"\b(dni|day|jutro|pojutrze|72 ?h|48 ?h)\b", lower):
        return "days"
    if re.search(r"\b(godzin|hour|h12|h24|dzisiaj|12 ?h|24 ?h)\b", lower):
        return "hours"
    return "hours"


def summarize_agent_response(
    *,
    agent_id: str,
    response: str | None,
    error: str | None,
    status: str,
) -> AgentRunSummary:
    raw_response = (response or "").strip()
    if status != "completed" or not raw_response:
        perspective = error or "Brak odpowiedzi agenta."
        return AgentRunSummary(
            perspective=perspective,
            concerns=[perspective],
            recommendations=["Zweryfikowac brak perspektywy i uzupelnic dane zastepcze."],
            urgency="immediate" if "timed out" in (error or "").lower() else "hours",
        )

    sections = {
        "concerns": [
            _extract_section(raw_response, "Ryzyka wykonania i skutki uboczne"),
            _extract_section(raw_response, "Luki informacyjne", level="##"),
            _extract_section(raw_response, "Ryzyka", level="###"),
            _extract_section(raw_response, "Obawy", level="###"),
            _extract_section(raw_response, "Ograniczenia", level="###"),
        ],
        "recommendations": [
            _extract_section(raw_response, "Rekomendowany wariant", level="##"),
            _extract_section(raw_response, "Zalecenia", level="###"),
            _extract_section(raw_response, "Dzialania", level="###"),
            _extract_section(raw_response, "Priorytety", level="###"),
        ],
    }

    perspective = _first_paragraph(raw_response)
    if not perspective:
        perspective = _sentences(raw_response)[0] if _sentences(raw_response) else "Brak syntetycznej perspektywy."

    concerns = _unique_keep_order(
        [*sum((_extract_bullets(block) for block in sections["concerns"] if block), []), *[
            line for line in _collect_lines(raw_response) if re.search(r"ryzyk|zagroz|brak|problem|waskie gard|obaw|eskal", line.lower())
        ]],
        limit=3,
    )
    if not concerns:
        concerns = ["Brak wyraznie nazwanych ryzyk w odpowiedzi agenta."]

    recommendations = _unique_keep_order(
        [*sum((_extract_bullets(block) for block in sections["recommendations"] if block), []), *[
            line for line in _collect_lines(raw_response) if re.search(r"rekomend|zalec|priorytet|dzialan|powin", line.lower())
        ]],
        limit=3,
    )

    if agent_id == "orchestrator":
        recommendation_match = _RECOMMENDATION_HEADING_RE.search(raw_response)
        recommended_variant = (
            recommendation_match.group("bold") or recommendation_match.group("plain")
            if recommendation_match
            else None
        )
        if recommended_variant:
            recommendations = _unique_keep_order([recommended_variant.strip(), *recommendations], limit=3)

    if not recommendations:
        first_sentence = _sentences(raw_response)[0] if _sentences(raw_response) else ""
        recommendations = [first_sentence or "Kontynuowac monitoring i aktualizowac plan dzialan."]

    return AgentRunSummary(
        perspective=perspective,
        concerns=concerns,
        recommendations=recommendations,
        urgency=_detect_urgency(raw_response),
    )


def _slugify_type(text: str) -> str:
    normalized = _normalize_label(text)
    return normalized.replace(" ", "_")[:64] or "custom"


def _extract_estimated_cost(text: str) -> str:
    for line in _collect_lines(text):
        if re.search(r"\bsuma\b", line.lower()):
            value = line.split(":", 1)[1].strip() if ":" in line else line
            return value
    return "Brak danych"


def _extract_time_to_resolve(harmonogram_section: str) -> str:
    matches = re.findall(r"(?im)^####\s+([^\n]+)", harmonogram_section)
    if not matches:
        return "Brak danych"
    last = matches[-1].strip()
    return f"Do {last}" if re.search(r"\d", last) else last


def _parse_variant_actions(body: str) -> ScenarioActions:
    harmonogram = _extract_section(body, "Harmonogram dzialan")
    return ScenarioActions(
        h2=_unique_keep_order(_extract_bullets(_extract_subsection(harmonogram, "0-2 h")) or _extract_bullets(_extract_subsection(harmonogram, "0–2 h"))),
        h12=_unique_keep_order(_extract_bullets(_extract_subsection(harmonogram, "2-12 h")) or _extract_bullets(_extract_subsection(harmonogram, "2–12 h"))),
        h24=_unique_keep_order(
            _extract_bullets(_extract_subsection(harmonogram, "12-24 h (i dalej jesli potrzebne)"))
            or _extract_bullets(_extract_subsection(harmonogram, "12–24 h (i dalej jesli potrzebne)"))
            or _extract_bullets(_extract_subsection(harmonogram, "12-24 h"))
            or _extract_bullets(_extract_subsection(harmonogram, "12–24 h"))
        ),
    )


def _parse_variant(title: str, body: str, *, index: int, recommended_title: str | None) -> Scenario:
    clean_body = re.split(r"(?m)^#\s+REKOMENDACJA ORCHESTRATORA\b", body, maxsplit=1)[0]
    actions = _parse_variant_actions(clean_body)
    risks = _unique_keep_order(_extract_bullets(_extract_section(clean_body, "Ryzyka wykonania i skutki uboczne")), limit=6)
    benefits = _unique_keep_order(_extract_bullets(_extract_section(clean_body, "Zalety w tym kontekscie incydentu")), limit=6)
    if not benefits:
        benefits = _unique_keep_order(_extract_bullets(_extract_section(clean_body, "Zalety w tym kontekście incydentu")), limit=6)
    consequences = _extract_section(clean_body, "Konsekwencje odrzucenia tego wariantu")
    costs = _extract_estimated_cost(_extract_section(clean_body, "Koszt - rozliczenie") or _extract_section(clean_body, "Koszt — rozliczenie"))
    harmonogram = _extract_section(clean_body, "Harmonogram dzialan")
    label = chr(65 + index)
    return Scenario(
        id=str(uuid4()),
        label=label,
        title=title,
        type=_slugify_type(title),
        estimated_cost=costs,
        time_to_resolve=_extract_time_to_resolve(harmonogram),
        is_recommended=_normalize_label(title) == _normalize_label(recommended_title or ""),
        risks=risks,
        benefits=benefits,
        consequences_of_inaction=consequences or "Brak danych o konsekwencjach odrzucenia.",
        actions=actions,
    )


def build_scenario_version_from_orchestrator_report(
    *,
    report: str,
    run_id: str,
    incident_id: str,
    fallback_confidence: float,
) -> ScenarioVersion | None:
    normalized_report = (report or "").strip()
    if not normalized_report:
        return None

    parts = re.split(r"(?m)^##\s+Wariant strategiczny:\s+(.+?)\s*$", normalized_report)
    if len(parts) < 3:
        return None

    recommendation_match = _RECOMMENDATION_HEADING_RE.search(normalized_report)
    recommended_title = None
    if recommendation_match:
        recommended_title = (recommendation_match.group("bold") or recommendation_match.group("plain") or "").strip()

    scenarios: list[Scenario] = []
    for index in range(1, len(parts), 2):
        title = parts[index].strip()
        body = parts[index + 1] if index + 1 < len(parts) else ""
        if not title:
            continue
        scenarios.append(_parse_variant(title, body, index=(index - 1) // 2, recommended_title=recommended_title))

    if not scenarios:
        return None

    if recommended_title:
        for scenario in scenarios:
            scenario.is_recommended = _normalize_label(scenario.title) == _normalize_label(recommended_title)

    if not any(scenario.is_recommended for scenario in scenarios):
        scenarios[0].is_recommended = True

    recommended = next((scenario for scenario in scenarios if scenario.is_recommended), scenarios[0])
    rationale = _extract_section(normalized_report, "Uzasadnienie (fakty + liczby)", level="##")
    if not rationale:
        rationale = _extract_section(normalized_report, "Niepewnosc i warunki", level="##")

    return ScenarioVersion(
        id=str(uuid4()),
        run_id=run_id,
        incident_id=incident_id,
        created_at=datetime.now(UTC),
        recommendation_label=recommended.label,
        confidence=round(fallback_confidence, 2),
        scenarios=scenarios,
        rationale=rationale or "Scenariusze wyprowadzone z raportu orchestratora.",
    )
