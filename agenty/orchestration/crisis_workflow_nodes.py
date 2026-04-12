"""LangGraph node bodies: Mongo step tracing + council execution + phone enrichment + synthesis."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agenty.connection import LlmConnection
from agenty.orchestration.agent_runner import AgentRunner
from agenty.orchestration.agent_selector import AgentSelector
from agenty.orchestration.crisis_graph_state import CrisisGraphState
from agenty.orchestration.exceptions import WorkflowPause
from agenty.orchestration.hierarchy_service import HierarchyService
from agenty.orchestration.models import AgentRun, AgentRunSummary, ExternalInfoRequest, WorkflowRun, WorkflowState, WorkflowStep
from agenty.orchestration.reconciliation import ReconciliationService
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.response_parsers import summarize_agent_response
from agenty.orchestration.scenario_service import ScenarioService
from agenty.orchestration.tracing import trace_event, trace_human_block

if TYPE_CHECKING:
    from agenty.mcp_gateway.base import MCPGateway

logger = logging.getLogger(__name__)

_COUNCIL_INSTRUCTION = (
    "Jestes czlonkiem rady agentow CrisisTwin. Pozostale role z sekcji 'Rada' "
    "otrzymuja ten sam incydent i odpowiadaja rownolegle. Po zebraniu glosow "
    "osobny orchestrator porowna zgodnosci, konflikty i zaleznosci. "
    "Skup sie na swojej roli, liczbach, ryzykach i priorytetach."
)

_ORCHESTRATOR_PROMPT = (
    "Na podstawie incydentu, odpowiedzi rady, rekonsyliacji i ewentualnych "
    "potwierdzen telefonicznych przygotuj pelny raport orchestratora zgodnie z instrukcja systemowa. "
    "Bazuj przede wszystkim na streszczeniach operacyjnych rady. "
    "Do fragmentow zrodlowych siegaj tylko wtedy, gdy streszczenie jest niepelne albo agent nie odpowiedzial. "
    "Nie wymyslaj danych spoza materialu. Kazda liczbe oznacz jako WIADOME, SZACUNEK albo NIEZNANE, "
    "a scenariusze zbuduj wylacznie z roznic i decyzji wynikajacych z tej rady."
)

_PHONE_PLANNER_SYSTEM = (
    "Decydujesz, czy w kryzysie potrzebne jest jedno dodatkowe polaczenie telefoniczne do zasobu. "
    "Zwroc tylko JSON, bez komentarzy i markdownu."
)

_PHONE_PLANNER_OUTPUT_HINT = {
    "should_call": True,
    "resource_id": "res_001",
    "reason": "Potrzebne telefoniczne potwierdzenie dostepnosci zasobu.",
    "requirements": "Potwierdz dostepnosc, ograniczenia i najwazniejsze liczby operacyjne.",
    "schema": {
        "type": "object",
        "properties": {
            "availability": {"type": "string", "description": "Czy zasob jest dostepny teraz"},
            "capacity": {"type": "string", "description": "Najwazniejsza liczba lub pojemnosc"},
            "constraints": {"type": "string", "description": "Ograniczenia i ETA"},
        },
        "required": ["availability", "capacity"],
    },
}

_OWNER_AGENT_BY_TYPE = {
    "hospital": "dyrektor-szpitala",
    "szpital": "dyrektor-szpitala",
    "fire": "komendant-psp",
    "psp": "komendant-psp",
    "police": "komendant-policji",
    "policja": "komendant-policji",
    "fuel": "logistyk",
    "logistyka": "logistyk",
    "logistics": "logistyk",
    "transport": "logistyk",
    "paliwo": "logistyk",
    "operator": "dyrektor-abw",
    "zaklad": "dyrektor-abw",
    "plant": "dyrektor-abw",
    "scada": "dyrektor-abw",
    "cyber": "dyrektor-abw",
    "it": "dyrektor-abw",
    "wios": "wojewoda",
    "gis": "wojewoda",
    "sanepid": "wojewoda",
    "environment": "wojewoda",
    "chem": "wojewoda",
    "lab": "wojewoda",
    "laboratory": "wojewoda",
    "rcb": "wojewoda",
    "coordination": "wojewoda",
}

_GAP_RESOURCE_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("szpital", "sor", "oiom", "pacjent", "lozek", "lpr", "medycz"),
        ("hospital", "szpital", "medical", "medycz", "dyrektor-szpitala"),
    ),
    (
        ("seveso", "scada", "cyber", "operator", "zaklad", "właściciel", "wlasciciel", "instalacj", "sabota"),
        ("operator", "zaklad", "plant", "scada", "cyber", "it"),
    ),
    (
        ("wios", "gis", "emisj", "chemicz", "toksy", "pm10", "srodowisk", "powietrz", "sanepid"),
        ("wios", "gis", "environment", "chem", "lab", "laboratory", "sanepid"),
    ),
    (
        ("rcb", "inne incydenty", "kraj", "koordynac"),
        ("rcb", "coordination", "center", "centrum", "wojewoda"),
    ),
    (
        ("policj", "ruch", "objazd", "blokad"),
        ("police", "policja", "komendant-policji"),
    ),
    (
        ("paliwo", "transport", "logist", "eta", "zasob"),
        ("fuel", "transport", "logist", "paliwo"),
    ),
)

_FALLBACK_CONTACT_META: tuple[tuple[tuple[str, ...], dict[str, str]], ...] = (
    (
        ("seveso", "scada", "cyber", "operator", "zaklad", "instalacj", "sabota"),
        {"type": "operator", "name": "Kontakt operatora instalacji", "contact_role": "operator instalacji"},
    ),
    (
        ("wios", "gis", "emisj", "chemicz", "toksy", "pm10", "srodowisk", "powietrz", "sanepid"),
        {"type": "wios", "name": "Kontakt monitoringu srodowiskowego", "contact_role": "koordynator WIOS"},
    ),
    (
        ("szpital", "sor", "oiom", "pacjent", "medycz", "lpr"),
        {"type": "hospital", "name": "Kontakt medyczny dyzurny", "contact_role": "dyrektor-szpitala"},
    ),
    (
        ("policj", "ruch", "objazd", "blokad"),
        {"type": "police", "name": "Kontakt policyjny dyzurny", "contact_role": "komendant-policji"},
    ),
    (
        ("paliwo", "transport", "logist", "eta", "zasob"),
        {"type": "logistics", "name": "Kontakt logistyczny dyzurny", "contact_role": "logistyk"},
    ),
)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _synthetic_resource_for_gaps(*, phone_number: str, gaps: list[str]) -> dict[str, Any]:
    joined = " ".join(_normalize_text(item) for item in gaps)
    meta = {"type": "external", "name": "Kontakt kryzysowy AI", "contact_role": "koordynator zewnetrzny"}
    for tokens, candidate in _FALLBACK_CONTACT_META:
        if any(token in joined for token in tokens):
            meta = candidate
            break
    resource_type = str(meta["type"])
    return {
        "resource_id": f"fallback_{resource_type}_phone",
        "name": str(meta["name"]),
        "type": resource_type,
        "status": "standby",
        "contact_phone": phone_number,
        "contact_name": "Kontakt telefoniczny AI",
        "contact_role": str(meta["contact_role"]),
    }


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


def _step_input_payload(state: CrisisGraphState, step: WorkflowState) -> dict[str, Any]:
    if step == "fetch_hierarchy":
        return {}
    order: list[WorkflowState] = [
        "fetch_hierarchy",
        "select_agents",
        "run_agents_async",
        "resolve_conflicts",
        "plan_external_info",
        "await_external_info",
        "refresh_agent_after_call",
        "run_orchestrator",
        "generate_scenarios",
        "sync_resources",
    ]
    idx = order.index(step)
    if idx == 0:
        return {}
    previous = state.get(order[idx - 1])
    return _json_safe(previous) if isinstance(previous, dict) else {}


def _summary_payload(summary: AgentRunSummary | None) -> dict[str, Any] | None:
    return summary.model_dump(mode="json") if summary else None


def _truncate(text: str, *, limit: int = 12_000) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n... [truncated]"


def _latest_agent_runs(agent_runs: list[AgentRun], *, include_orchestrator: bool = False) -> list[AgentRun]:
    latest: dict[str, AgentRun] = {}
    order: list[str] = []
    for item in agent_runs:
        if not include_orchestrator and item.agent_id == "orchestrator":
            continue
        if item.agent_id not in order:
            order.append(item.agent_id)
        latest[item.agent_id] = item
    return [latest[agent_id] for agent_id in order if agent_id in latest]


def _render_council_sources(agent_runs: list[AgentRun]) -> str:
    blocks: list[str] = []
    for run in agent_runs:
        lines = [f"## {run.agent_id}", f"status: {run.status}"]
        if run.summary:
            lines.extend(
                [
                    f"perspective: {run.summary.perspective}",
                    "concerns:",
                    *[f"- {item}" for item in run.summary.concerns[:3]],
                    "recommendations:",
                    *[f"- {item}" for item in run.summary.recommendations[:3]],
                    f"urgency: {run.summary.urgency}",
                ]
            )
        if not run.summary or run.status != "completed":
            body = run.response or run.error or "(empty)"
            lines.extend(["source_excerpt:", _truncate(body, limit=1_500)])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    value = text.strip()
    candidates = [value]
    if "```" in value:
        candidates.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", value, re.DOTALL))
    match = re.search(r"\{.*\}", value, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _resource_owner_agent(resource: dict[str, Any]) -> str | None:
    for raw in (resource.get("contact_role"), resource.get("type"), resource.get("name")):
        normalized = _normalize_text(raw)
        for token, agent_id in _OWNER_AGENT_BY_TYPE.items():
            if token in normalized:
                return agent_id
    return None


def _fallback_schema_for_resource(resource: dict[str, Any]) -> dict[str, Any]:
    normalized_type = _normalize_text(resource.get("type"))
    if "hospital" in normalized_type or "szpital" in normalized_type:
        return {"type": "object", "properties": {"availability": {"type": "string"}, "capacity": {"type": "string"}, "constraints": {"type": "string"}}, "required": ["availability", "capacity"]}
    if "fire" in normalized_type or "psp" in normalized_type:
        return {"type": "object", "properties": {"availability": {"type": "string"}, "equipment": {"type": "string"}, "constraints": {"type": "string"}}, "required": ["availability", "equipment"]}
    if "police" in normalized_type or "policja" in normalized_type:
        return {"type": "object", "properties": {"availability": {"type": "string"}, "coverage": {"type": "string"}, "constraints": {"type": "string"}}, "required": ["availability", "coverage"]}
    return {"type": "object", "properties": {"availability": {"type": "string"}, "capacity": {"type": "string"}, "constraints": {"type": "string"}}, "required": ["availability"]}


def _validate_schema(schema_def: Any, resource: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema_def, dict) or schema_def.get("type") != "object":
        return _fallback_schema_for_resource(resource)
    properties = schema_def.get("properties")
    if not isinstance(properties, dict) or not properties:
        return _fallback_schema_for_resource(resource)
    required = schema_def.get("required")
    if required is not None and not isinstance(required, list):
        schema_def = dict(schema_def)
        schema_def["required"] = []
    return schema_def


def _fallback_external_info_plan(*, incident: dict[str, Any], reconciliation: dict[str, Any], resources: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [item for item in resources if str(item.get("contact_phone", "")).strip()]
    if not candidates:
        return {"should_call": False, "reason": "Brak zasobow z numerem kontaktowym."}
    resource = next((item for item in candidates if _resource_owner_agent(item) == "dyrektor-szpitala"), candidates[0])
    priority = _normalize_text(incident.get("priority"))
    should_call = bool(reconciliation.get("gaps") or reconciliation.get("conflicts") or priority in {"critical", "high"})
    if not should_call:
        return {"should_call": False, "reason": "Rada nie wskazuje blokujacej luki wymagajacej telefonu."}
    return {
        "should_call": True,
        "resource_id": resource.get("resource_id"),
        "reason": "Potrzebne telefoniczne potwierdzenie dostepnosci zasobu i ograniczen operacyjnych.",
        "requirements": f"Potwierdz dla zasobu '{resource.get('name', 'zasob')}' aktualna dostepnosc, kluczowe liczby operacyjne, ETA i ograniczenia.",
        "schema": _fallback_schema_for_resource(resource),
    }


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = _normalize_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item.strip())
    return result


def _extract_explicit_unknowns(text: str) -> list[str]:
    if not text.strip():
        return []
    lines = [line.rstrip() for line in text.splitlines()]
    gaps: list[str] = []
    capture = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if capture:
                capture = False
            continue
        normalized = _normalize_text(line)
        if re.search(r"czego nie wiem|czego nie wiemy|luki analityczne|braki informacyjne|open questions", normalized):
            capture = True
            continue
        if capture:
            if line.startswith("#"):
                capture = False
                continue
            if re.match(r"^[-*]|\d+[.)]", line):
                gaps.append(strip := strip_bullet_prefix(line))
                continue
            if re.search(r"nieznan|brak danych|nie wiem|unknown", normalized):
                gaps.append(line)
                continue
            capture = False
        if re.search(r"\bnieznane\b|brak danych|nie wiem|unknown", normalized):
            gaps.append(strip_bullet_prefix(line))
    return _dedupe_preserve_order([item for item in gaps if item])


def strip_bullet_prefix(line: str) -> str:
    return re.sub(r"^[-*\d.)\s]+", "", line).strip()


def _collect_external_info_gaps(*, reconciliation: dict[str, Any], agent_runs: list[AgentRun]) -> list[str]:
    collected: list[str] = []
    for value in reconciliation.get("gaps", []) if isinstance(reconciliation.get("gaps"), list) else []:
        if isinstance(value, str):
            collected.append(strip_bullet_prefix(value))
    for run in agent_runs:
        if isinstance(run.summary, AgentRunSummary):
            for item in run.summary.concerns:
                if isinstance(item, str) and re.search(r"nieznan|brak danych|nie wiem|unknown", _normalize_text(item)):
                    collected.append(strip_bullet_prefix(item))
        body = (run.response or "").strip()
        collected.extend(_extract_explicit_unknowns(body))
    return _dedupe_preserve_order([item for item in collected if item])


def _score_resource_for_gaps(resource: dict[str, Any], gaps: list[str]) -> int:
    haystack = " ".join(
        filter(
            None,
            [
                _normalize_text(resource.get("type")),
                _normalize_text(resource.get("contact_role")),
                _normalize_text(resource.get("name")),
            ],
        )
    )
    if not haystack:
        return 0

    score = 0
    joined_gaps = " ".join(_normalize_text(item) for item in gaps)
    for gap_tokens, resource_tokens in _GAP_RESOURCE_HINTS:
        if any(token in joined_gaps for token in gap_tokens):
            if any(token in haystack for token in resource_tokens):
                score += 3
    if _resource_owner_agent(resource):
        score += 1
    return score


def _pick_resource_for_gaps(resources: list[dict[str, Any]], gaps: list[str]) -> dict[str, Any] | None:
    if not resources:
        return None
    if not gaps:
        return resources[0]
    ranked = sorted(
        resources,
        key=lambda item: (_score_resource_for_gaps(item, gaps), bool(_resource_owner_agent(item))),
        reverse=True,
    )
    return ranked[0]


def _forced_phone_plan(*, resource: dict[str, Any], gaps: list[str]) -> dict[str, Any]:
    gap_lines = "\n".join(f"- {item}" for item in gaps[:6]) or "- Potwierdz brakujace dane operacyjne."
    reason = (
        "Rada agentow wskazala jawne luki informacyjne wymagajace telefonicznego potwierdzenia: "
        + "; ".join(gaps[:3])
    )
    requirements = (
        f"Masz uzupelnic brakujace dane dla incydentu, rozmawiajac z kontaktem zasobu '{resource.get('name', 'zasob')}'.\n"
        "Zadaj konkretne pytania i zbierz tylko informacje, ktorych rada agentow wprost nie zna.\n"
        "Luki do potwierdzenia:\n"
        f"{gap_lines}\n"
        "Dopytaj o liczby, ETA, ograniczenia i to, co mozna potwierdzic operacyjnie teraz."
    )
    return {
        "should_call": True,
        "resource_id": resource.get("resource_id"),
        "reason": reason,
        "requirements": requirements,
        "schema": _fallback_schema_for_resource(resource),
    }


def _call_notice(request: ExternalInfoRequest | None, *, completed: bool = False) -> str | None:
    if request is None:
        return None
    return (
        f"Rozmowa z {request.resource_name} zakonczona. Trwa odswiezanie odpowiedzi agenta."
        if completed
        else f"Trwa rozmowa z {request.resource_name}."
    )


class CrisisWorkflowNodes:
    """One async node per workflow state; updates WorkflowStep rows like the legacy engine."""

    def __init__(
        self,
        *,
        repository: OrchestrationRepository,
        hierarchy: HierarchyService,
        selector: AgentSelector,
        runner: AgentRunner,
        reconciliation: ReconciliationService,
        scenarios: ScenarioService,
        mcp: MCPGateway,
        planner_llm: LlmConnection,
        phone_poll_interval_s: float,
        phone_max_wait_s: float,
        phone_agent_default_phone_number: str | None = None,
    ) -> None:
        self._repository = repository
        self._hierarchy = hierarchy
        self._selector = selector
        self._runner = runner
        self._reconciliation = reconciliation
        self._scenarios = scenarios
        self._mcp = mcp
        self._planner_llm = planner_llm
        self._heartbeat_interval_s = 5.0
        self._phone_poll_interval_s = max(1.0, phone_poll_interval_s)
        self._phone_max_wait_s = max(self._phone_poll_interval_s, phone_max_wait_s)
        self._phone_agent_default_phone_number = (phone_agent_default_phone_number or "").strip() or None

    def _run(self, state: CrisisGraphState) -> WorkflowRun:
        run = self._repository.get_run(state["run_id"])
        if run is None:
            raise KeyError(f"Unknown run id {state['run_id']}")
        return run

    def _begin_step(self, run: WorkflowRun, name: WorkflowState, state: CrisisGraphState) -> WorkflowStep:
        trace_event("orchestration.step.start", run_id=run.id, state=name)
        self._repository.update_run_state(run.id, status=name, current_state=name)
        previous = self._repository.get_step(run.id, name)
        step = WorkflowStep(run_id=run.id, state=name, status="running", attempts=((previous.attempts + 1) if previous and previous.status != "completed" else previous.attempts) if previous else 0, started_at=datetime.now(UTC), updated_at=datetime.now(UTC), input_payload=_step_input_payload(state, name))
        self._repository.upsert_step(step)
        return step

    def _complete_step(self, run: WorkflowRun, step: WorkflowStep, name: WorkflowState, output: dict[str, Any], *, mark_run_completed: bool) -> None:
        safe_output = _json_safe(output)
        step.status = "completed"
        step.output_payload = safe_output
        step.updated_at = datetime.now(UTC)
        step.finished_at = datetime.now(UTC)
        self._repository.upsert_step(step)
        trace_event("orchestration.step.complete", run_id=run.id, state=name, output_keys=list(safe_output.keys()))
        if mark_run_completed:
            self._repository.update_run_state(run.id, status="completed", current_state="completed", completed=True)
        else:
            self._repository.update_run_state(run.id, status=name, current_state=name, last_error=None)

    def _pause_step(self, run: WorkflowRun, step: WorkflowStep, name: WorkflowState, output: dict[str, Any], *, reason: str) -> None:
        step.status = "running"
        step.output_payload = _json_safe(output)
        step.updated_at = datetime.now(UTC)
        step.error = None
        self._repository.upsert_step(step)
        self._repository.update_run_state(run.id, status=name, current_state=name, last_error=None, completed=False)
        trace_event("orchestration.step.pause", run_id=run.id, state=name, reason=reason)

    def _fail_step(self, run: WorkflowRun, step: WorkflowStep, name: WorkflowState, exc: BaseException, *, fail_run: bool = True) -> None:
        step.status = "failed"
        step.error = str(exc)
        step.updated_at = datetime.now(UTC)
        step.finished_at = datetime.now(UTC)
        self._repository.upsert_step(step)
        if fail_run:
            self._repository.update_run_state(run.id, status="failed", current_state="failed", last_error=str(exc), completed=True)
        else:
            self._repository.update_run_state(run.id, status=name, current_state=name, last_error=str(exc), completed=False)

    async def _heartbeat_loop(self, run_id: str, name: WorkflowState, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._heartbeat_interval_s)
            except TimeoutError:
                self._repository.touch_step(run_id, name)
                self._repository.touch_run(run_id, current_state=name)

    @asynccontextmanager
    async def _heartbeat(self, run_id: str, name: WorkflowState):
        stop = asyncio.Event()
        task = asyncio.create_task(self._heartbeat_loop(run_id, name, stop))
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            self._repository.touch_step(run_id, name)
            self._repository.touch_run(run_id, current_state=name)

    def _latest_council_runs(self, run_id: str) -> list[AgentRun]:
        return _latest_agent_runs(self._repository.list_agent_runs(run_id), include_orchestrator=False)

    async def fetch_hierarchy(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "fetch_hierarchy"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            context = self._hierarchy.load_context(run.incident_id)
            output = _json_safe({"organization": context["organization"], "incident": context["incident"], "hierarchy_found": bool(context["hierarchy"]), "hierarchy": context["hierarchy"]})
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def select_agents(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "select_agents"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            selected = self._selector.select(hierarchy=dict(fetched.get("hierarchy", {})), incident=dict(fetched.get("incident", {})))
            output = {"agents": selected}
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def run_agents_async(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "run_agents_async"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            selected = state.get("select_agents")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            if not isinstance(selected, dict):
                raise TypeError("select_agents payload must be a dict")
            agent_ids = list(selected.get("agents", []))
            incident = dict(fetched.get("incident", {}))
            context_sections = {
                "Incident": json.dumps(incident, ensure_ascii=False, default=_json_default),
                "Organization": json.dumps(fetched.get("organization", {}), ensure_ascii=False, default=_json_default),
                "Rada": f"Role w tej turze (odpowiedzi rownolegle): {', '.join(agent_ids) if agent_ids else '(brak)'}.\n{_COUNCIL_INSTRUCTION}",
            }
            prompt = "Przeanalizuj incydent z perspektywy swojej roli. Podaj najwazniejsze dzialania, ryzyka, zaleznosci czasowe i priorytety. Uzywaj konkretow, liczb i ograniczen, gdy sa dostepne."
            async with self._heartbeat(run.id, name):
                runs = await self._runner.run(agent_ids, prompt, context_sections)
            for item in runs:
                item.run_id = run.id
                item.summary = summarize_agent_response(agent_id=item.agent_id, response=item.response, error=item.error, status=item.status)
                self._repository.append_agent_run(item)
            failures = [item for item in runs if item.status != "completed"]
            output = {"total": len(runs), "failures": len(failures), "failed_agents": [item.agent_id for item in failures], "council_agents": [item.agent_id for item in runs]}
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def resolve_conflicts(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "resolve_conflicts"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            recon = self._reconciliation.reconcile(self._latest_council_runs(run.id))
            self._complete_step(run, step, name, recon, mark_run_completed=False)
            return {name: _json_safe(recon)}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def plan_external_info(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "plan_external_info"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            recon = state.get("resolve_conflicts")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            if not isinstance(recon, dict):
                raise TypeError("resolve_conflicts payload must be a dict")

            incident = dict(fetched.get("incident", {}))
            async with self._heartbeat(run.id, name):
                raw_resource_response = await asyncio.to_thread(self._mcp.call_tool, "resource_list", {"incident_id": run.incident_id})
            live_resources = json.loads(raw_resource_response)
            resources = live_resources if isinstance(live_resources, list) and live_resources else list(incident.get("resources", []))
            latest_council_runs = self._latest_council_runs(run.id)
            council_summary = [
                {"agent_id": item.agent_id, "status": item.status, "summary": _summary_payload(item.summary)}
                for item in latest_council_runs
            ]
            explicit_gaps = _collect_external_info_gaps(reconciliation=recon, agent_runs=latest_council_runs)
            candidate_resources = [dict(item) for item in resources if isinstance(item, dict) and str(item.get("contact_phone", "")).strip()]
            if not candidate_resources and explicit_gaps and self._phone_agent_default_phone_number:
                candidate_resources = [
                    _synthetic_resource_for_gaps(
                        phone_number=self._phone_agent_default_phone_number,
                        gaps=explicit_gaps,
                    )
                ]
            if not candidate_resources:
                output = {
                    "should_call": False,
                    "reason": "Brak zasobow z numerem kontaktowym.",
                    "explicit_gaps": explicit_gaps,
                }
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}

            preferred_resource = _pick_resource_for_gaps(candidate_resources, explicit_gaps)
            planner_messages = [
                {"role": "system", "content": _PHONE_PLANNER_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "incident": incident,
                            "council_summary": council_summary,
                            "reconciliation": recon,
                            "resources": candidate_resources,
                            "explicit_unknowns": explicit_gaps,
                            "preferred_resource_id": preferred_resource.get("resource_id") if isinstance(preferred_resource, dict) else None,
                        },
                        ensure_ascii=False,
                        default=_json_default,
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Zwroc jeden JSON decydujacy, czy wykonac telefon. "
                        "Jesli rada agentow wprost wskazuje sekcje 'Czego nie wiem', 'NIEZNANE', "
                        "brak danych albo luki analityczne i istnieje numer kontaktowy, ustaw should_call=true. "
                        "Przyklad:\n" + json.dumps(_PHONE_PLANNER_OUTPUT_HINT, ensure_ascii=False)
                    ),
                },
            ]
            async with self._heartbeat(run.id, name):
                planner_reply = await asyncio.to_thread(self._planner_llm.chat_completion, planner_messages, log_label="workflow:plan_external_info")
            plan = _extract_json_object(planner_reply) or _fallback_external_info_plan(incident=incident, reconciliation=recon, resources=candidate_resources)

            if explicit_gaps and not bool(plan.get("should_call")):
                plan = _forced_phone_plan(resource=preferred_resource or candidate_resources[0], gaps=explicit_gaps)

            if not bool(plan.get("should_call")):
                output = {
                    "should_call": False,
                    "reason": str(plan.get("reason") or "Brak telefonu w tej turze."),
                    "explicit_gaps": explicit_gaps,
                }
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}

            resource_id = str(plan.get("resource_id") or "").strip()
            selected_resource = next((item for item in candidate_resources if str(item.get("resource_id")) == resource_id), None)
            if selected_resource is None:
                if explicit_gaps:
                    selected_resource = preferred_resource or candidate_resources[0]
                    plan = _forced_phone_plan(resource=selected_resource, gaps=explicit_gaps)
                else:
                    fallback = _fallback_external_info_plan(incident=incident, reconciliation=recon, resources=candidate_resources)
                    if not fallback.get("should_call"):
                        output = {
                            "should_call": False,
                            "reason": str(fallback.get("reason") or "Planner nie wybral zasobu."),
                            "explicit_gaps": explicit_gaps,
                        }
                        self._complete_step(run, step, name, output, mark_run_completed=False)
                        return {name: output}
                    selected_resource = next((item for item in candidate_resources if str(item.get("resource_id")) == str(fallback.get("resource_id") or "")), candidate_resources[0])
                    plan = fallback

            if selected_resource is None:
                output = {
                    "should_call": False,
                    "reason": "Planner nie wybral zasobu.",
                    "explicit_gaps": explicit_gaps,
                }
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}

            if explicit_gaps and preferred_resource is not None:
                selected_score = _score_resource_for_gaps(selected_resource, explicit_gaps)
                preferred_score = _score_resource_for_gaps(preferred_resource, explicit_gaps)
                if preferred_score > selected_score:
                    selected_resource = preferred_resource
                    plan = _forced_phone_plan(resource=selected_resource, gaps=explicit_gaps)

            request = ExternalInfoRequest(
                id=f"ext_{uuid4().hex[:12]}",
                run_id=run.id,
                incident_id=run.incident_id,
                resource_id=str(selected_resource.get("resource_id")),
                resource_name=str(selected_resource.get("name", "Zasob")),
                phone_number=str(selected_resource.get("contact_phone")),
                resource_type=str(selected_resource.get("type") or ""),
                contact_name=str(selected_resource.get("contact_name") or "") or None,
                contact_role=str(selected_resource.get("contact_role") or "") or None,
                owner_agent_id=_resource_owner_agent(selected_resource),
                schema_def=_validate_schema(plan.get("schema"), selected_resource),
                requirements=str(plan.get("requirements") or "").strip() or f"Potwierdz dla zasobu '{selected_resource.get('name', 'zasob')}' aktualna dostepnosc, kluczowe liczby operacyjne, ETA i ograniczenia.",
                reason=str(plan.get("reason") or "").strip() or None,
                status="planned",
                notice=f"Zaplanowano rozmowe z {selected_resource.get('name', 'zasob')}.",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self._repository.upsert_external_info_request(request)
            output = {
                "should_call": True,
                "resource_id": request.resource_id,
                "resource_name": request.resource_name,
                "owner_agent_id": request.owner_agent_id,
                "reason": request.reason,
                "requirements": request.requirements,
                "explicit_gaps": explicit_gaps,
            }
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def await_external_info(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "await_external_info"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            request = self._repository.get_external_info_request(run.id)
            if request is None:
                output = {"status": "skipped", "notice": "Brak telefonu w tej orkiestracji."}
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}

            if request.status in {"completed", "failed", "timed_out", "skipped"}:
                output = {"status": request.status, "call_id": request.call_id, "resource_id": request.resource_id, "resource_name": request.resource_name, "notice": request.notice, "result": _json_safe(request.result) if request.result is not None else None, "error": request.error, "updated_at": request.updated_at.isoformat()}
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}

            if not request.call_id:
                async with self._heartbeat(run.id, name):
                    start_raw = await asyncio.to_thread(self._mcp.call_tool, "phone_agent_start_call", {"phone_number": request.phone_number, "schema": request.schema_def, "requirements": request.requirements, "resource_name": request.resource_name})
                start_payload = json.loads(start_raw)
                call_id = str(start_payload.get("call_id") or "").strip()
                if not call_id:
                    request.status = "failed"
                    request.notice = f"Nie udalo sie zainicjowac rozmowy z {request.resource_name}."
                    request.error = str(start_payload.get("detail") or start_payload.get("error") or "missing call_id")
                    request.updated_at = datetime.now(UTC)
                    request.completed_at = datetime.now(UTC)
                    self._repository.upsert_external_info_request(request)
                    output = {"status": request.status, "resource_id": request.resource_id, "resource_name": request.resource_name, "call_id": None, "notice": request.notice, "error": request.error}
                    self._complete_step(run, step, name, output, mark_run_completed=False)
                    return {name: output}

                request.call_id = call_id
                request.status = "waiting"
                request.notice = _call_notice(request)
                request.updated_at = datetime.now(UTC)
                self._repository.upsert_external_info_request(request)
                output = {"status": request.status, "resource_id": request.resource_id, "resource_name": request.resource_name, "call_id": request.call_id, "notice": request.notice, "updated_at": request.updated_at.isoformat()}
                self._pause_step(run, step, name, output, reason=request.notice or "Oczekiwanie na rozmowe.")
                raise WorkflowPause(reason=request.notice or "waiting for phone call", delay_s=self._phone_poll_interval_s)

            elapsed_s = (datetime.now(UTC) - request.created_at).total_seconds()
            if elapsed_s >= self._phone_max_wait_s:
                request.status = "timed_out"
                request.notice = f"Rozmowa z {request.resource_name} przekroczyla limit oczekiwania."
                request.error = request.notice
                request.updated_at = datetime.now(UTC)
                request.completed_at = datetime.now(UTC)
                self._repository.upsert_external_info_request(request)
                output = {"status": request.status, "resource_id": request.resource_id, "resource_name": request.resource_name, "call_id": request.call_id, "notice": request.notice, "error": request.error}
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}

            async with self._heartbeat(run.id, name):
                poll_raw = await asyncio.to_thread(self._mcp.call_tool, "phone_agent_get_call", {"call_id": request.call_id})
            poll_payload = json.loads(poll_raw)
            external_status = _normalize_text(poll_payload.get("status"))
            if external_status == "completed":
                transcript_lines: list[str] = []
                transcript = poll_payload.get("transcript")
                if isinstance(transcript, list):
                    for item in transcript[-3:]:
                        if isinstance(item, dict) and str(item.get("text", "")).strip():
                            transcript_lines.append(f"{item.get('role', 'speaker')}: {str(item.get('text', '')).strip()}")
                request.status = "completed"
                request.result = poll_payload.get("result") if isinstance(poll_payload.get("result"), dict) else None
                request.transcript_excerpt = "\n".join(transcript_lines) or None
                request.notice = _call_notice(request, completed=True)
                request.error = None
                request.updated_at = datetime.now(UTC)
                request.completed_at = datetime.now(UTC)
                self._repository.upsert_external_info_request(request)
                if request.result:
                    self._repository.append_incident_update(run.incident_id, author_role=request.owner_agent_id or request.contact_role or "orchestrator", content=f"Telefoniczne potwierdzenie z {request.resource_name}: " + json.dumps(request.result, ensure_ascii=False), update_type="phone_verification")
                output = {"status": request.status, "resource_id": request.resource_id, "resource_name": request.resource_name, "call_id": request.call_id, "notice": request.notice, "result": _json_safe(request.result) if request.result is not None else None, "updated_at": request.updated_at.isoformat()}
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}

            if external_status == "failed":
                request.status = "failed"
                request.notice = f"Rozmowa z {request.resource_name} zakonczyla sie bledem."
                request.error = str(poll_payload.get("detail") or poll_payload.get("error") or "call failed")
                request.updated_at = datetime.now(UTC)
                request.completed_at = datetime.now(UTC)
                self._repository.upsert_external_info_request(request)
                output = {"status": request.status, "resource_id": request.resource_id, "resource_name": request.resource_name, "call_id": request.call_id, "notice": request.notice, "error": request.error}
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}

            request.status = "waiting"
            request.notice = _call_notice(request)
            request.updated_at = datetime.now(UTC)
            self._repository.upsert_external_info_request(request)
            output = {"status": request.status, "resource_id": request.resource_id, "resource_name": request.resource_name, "call_id": request.call_id, "notice": request.notice, "updated_at": request.updated_at.isoformat()}
            self._pause_step(run, step, name, output, reason=request.notice or "Oczekiwanie na rozmowe.")
            raise WorkflowPause(reason=request.notice or "waiting for phone call", delay_s=self._phone_poll_interval_s)
        except WorkflowPause:
            raise
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def refresh_agent_after_call(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "refresh_agent_after_call"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            request = self._repository.get_external_info_request(run.id)
            if request is None or request.status != "completed":
                output = {"status": "skipped", "reason": "Brak zakonczonego telefonu do odswiezenia odpowiedzi agenta."}
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}
            if not request.owner_agent_id:
                output = {"status": "skipped", "reason": "Brak przypisanego wlasciciela zasobu; wynik trafi tylko do orchestratora.", "resource_id": request.resource_id, "resource_name": request.resource_name}
                self._complete_step(run, step, name, output, mark_run_completed=False)
                return {name: output}

            previous_agent_run = next((item for item in reversed(self._repository.list_agent_runs(run.id)) if item.agent_id == request.owner_agent_id), None)
            context_sections = {
                "Incident": json.dumps(fetched.get("incident", {}), ensure_ascii=False, default=_json_default),
                "Organization": json.dumps(fetched.get("organization", {}), ensure_ascii=False, default=_json_default),
                "Telefoniczne potwierdzenie": json.dumps({"resource_id": request.resource_id, "resource_name": request.resource_name, "contact_name": request.contact_name, "contact_role": request.contact_role, "result": request.result, "transcript_excerpt": request.transcript_excerpt}, ensure_ascii=False, default=_json_default),
            }
            if previous_agent_run and previous_agent_run.response:
                context_sections["Poprzednia odpowiedz agenta"] = previous_agent_run.response
            prompt = "Masz nowe potwierdzenie telefoniczne o zasobie. Zaktualizuj analize ze swojej perspektywy, uwzgledniajac tylko dane potwierdzone w rozmowie. Napisz od nowa zwiezla, operacyjna odpowiedz z ryzykami, liczbami i rekomendacjami."
            async with self._heartbeat(run.id, name):
                refreshed = await self._runner.run([request.owner_agent_id], prompt, context_sections)
            refreshed_run = refreshed[0]
            refreshed_run.run_id = run.id
            refreshed_run.summary = summarize_agent_response(agent_id=refreshed_run.agent_id, response=refreshed_run.response, error=refreshed_run.error, status=refreshed_run.status)
            self._repository.append_agent_run(refreshed_run)
            output = {"status": refreshed_run.status, "agent_id": refreshed_run.agent_id, "resource_id": request.resource_id, "resource_name": request.resource_name, "notice": request.notice}
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc, fail_run=False)
            return {name: {"status": "failed", "error": str(exc)}}

    async def run_orchestrator(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "run_orchestrator"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            recon = state.get("resolve_conflicts")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            if not isinstance(recon, dict):
                raise TypeError("resolve_conflicts payload must be a dict")

            council_runs = self._latest_council_runs(run.id)
            council_summary = [
                {"agent_id": item.agent_id, "status": item.status, "summary": _summary_payload(item.summary), "error": item.error}
                for item in council_runs
            ]
            request = self._repository.get_external_info_request(run.id)
            context_sections = {
                "Incident": json.dumps(fetched.get("incident", {}), ensure_ascii=False, default=_json_default),
                "Organization": json.dumps(fetched.get("organization", {}), ensure_ascii=False, default=_json_default),
                "Rada - streszczenia": json.dumps(council_summary, ensure_ascii=False, indent=2),
                "Rada - odpowiedzi zrodlowe": _render_council_sources(council_runs),
                "Zgodnosci i konflikty": json.dumps(recon, ensure_ascii=False, indent=2, default=_json_default),
            }
            if request is not None:
                context_sections["Telefoniczne potwierdzenie"] = json.dumps({"status": request.status, "resource_id": request.resource_id, "resource_name": request.resource_name, "contact_name": request.contact_name, "contact_role": request.contact_role, "result": request.result, "notice": request.notice, "error": request.error}, ensure_ascii=False, indent=2, default=_json_default)
            async with self._heartbeat(run.id, name):
                result = await self._runner.run(["orchestrator"], _ORCHESTRATOR_PROMPT, context_sections, timeout_s=max(180.0, self._runner.default_timeout_s * 3))
            orchestrator_run = result[0]
            orchestrator_run.run_id = run.id
            orchestrator_run.summary = summarize_agent_response(agent_id=orchestrator_run.agent_id, response=orchestrator_run.response, error=orchestrator_run.error, status=orchestrator_run.status)
            self._repository.append_agent_run(orchestrator_run)
            output = {"agent_id": orchestrator_run.agent_id, "status": orchestrator_run.status, "has_report": bool(orchestrator_run.response), "report_chars": len(orchestrator_run.response or ""), "summary": _summary_payload(orchestrator_run.summary)}
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            failed_run = AgentRun(run_id=run.id, agent_id="orchestrator", status="failed", started_at=step.started_at, finished_at=datetime.now(UTC), latency_ms=max(0, int((datetime.now(UTC) - step.started_at).total_seconds() * 1000)), error=str(exc))
            failed_run.summary = summarize_agent_response(agent_id=failed_run.agent_id, response=failed_run.response, error=failed_run.error, status=failed_run.status)
            self._repository.append_agent_run(failed_run)
            self._fail_step(run, step, name, exc, fail_run=False)
            return {name: {"agent_id": "orchestrator", "status": "failed", "has_report": False, "report_chars": 0, "summary": _summary_payload(failed_run.summary)}}

    async def generate_scenarios(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "generate_scenarios"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            fetched = state.get("fetch_hierarchy")
            recon = state.get("resolve_conflicts")
            if not isinstance(fetched, dict):
                raise TypeError("fetch_hierarchy payload must be a dict")
            if not isinstance(recon, dict):
                raise TypeError("resolve_conflicts payload must be a dict")

            incident = dict(fetched.get("incident", {}))
            resource_count = len(incident.get("resources", []))
            agent_runs = self._repository.list_agent_runs(run.id)
            orchestrator_run = next((item for item in reversed(agent_runs) if item.agent_id == "orchestrator"), None)
            scenario_version = None
            source = "fallback"
            if orchestrator_run and orchestrator_run.status == "completed" and orchestrator_run.response:
                scenario_version = self._scenarios.build_from_orchestrator_report(run_id=run.id, incident_id=run.incident_id, report=orchestrator_run.response, reconciliation=recon)
                if scenario_version is not None:
                    source = "orchestrator"
            if scenario_version is None:
                scenario_version = self._scenarios.build(run_id=run.id, incident_id=run.incident_id, priority=str(incident.get("priority", "medium")), affected_population=int(incident.get("affected_population", 0)), resource_count=resource_count, reconciliation=recon)

            self._repository.save_scenario_version(scenario_version)
            self._repository.update_incident_links(run.incident_id, run_id=run.id, scenario_version_id=scenario_version.id)
            output = {"scenario_version_id": scenario_version.id, "recommended": scenario_version.recommendation_label, "source": source, "scenario_count": len(scenario_version.scenarios)}
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise

    async def sync_resources(self, state: CrisisGraphState) -> dict[str, Any]:
        name: WorkflowState = "sync_resources"
        run = self._run(state)
        step = self._begin_step(run, name, state)
        try:
            async with self._heartbeat(run.id, name):
                response = await asyncio.to_thread(self._mcp.call_tool, "resource_list", {"incident_id": run.incident_id})
            payload = json.loads(response)
            output = {"resource_sync": _json_safe(payload)}
            self._complete_step(run, step, name, output, mark_run_completed=False)
            return {name: output}
        except Exception as exc:  # noqa: BLE001
            self._fail_step(run, step, name, exc)
            raise
