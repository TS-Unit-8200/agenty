import json
from datetime import UTC, datetime, timedelta

from agenty.orchestration.agent_phone_tool import (
    PHONE_TOOL_NAME,
    CouncilPhoneToolProvider,
    poll_external_info_request,
)
from agenty.orchestration.exceptions import AgentToolPause
from agenty.orchestration.models import ExternalInfoRequest


class FakeRepository:
    def __init__(self) -> None:
        self.requests: list[ExternalInfoRequest] = []
        self.updates: list[tuple[str, str, str, str]] = []

    def get_external_info_request(self, run_id: str) -> ExternalInfoRequest | None:
        matches = [item for item in self.requests if item.run_id == run_id]
        if not matches:
            return None
        matches.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        return matches[0]

    def get_active_external_info_request(self, run_id: str) -> ExternalInfoRequest | None:
        matches = [
            item
            for item in self.requests
            if item.run_id == run_id and item.status in {"initiated", "waiting"}
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        return matches[0]

    def upsert_external_info_request(self, request: ExternalInfoRequest) -> ExternalInfoRequest:
        for index, existing in enumerate(self.requests):
            if existing.id == request.id:
                self.requests[index] = request
                break
        else:
            self.requests.append(request)
        return request

    def append_incident_update(
        self,
        incident_id: str,
        *,
        author_role: str,
        content: str,
        update_type: str,
    ) -> None:
        self.updates.append((incident_id, author_role, content, update_type))


class FakeMCP:
    def __init__(self, resources: list[dict[str, object]], *, call_status: str = "completed") -> None:
        self._resources = resources
        self._call_status = call_status
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(self, name: str, arguments: dict[str, object]) -> str:
        self.calls.append((name, arguments))
        if name == "resource_list":
            return json.dumps(self._resources, ensure_ascii=False)
        if name == "phone_agent_start_call":
            return json.dumps({"call_id": "call-123"}, ensure_ascii=False)
        if name == "phone_agent_get_call":
            if self._call_status == "completed":
                return json.dumps(
                    {
                        "status": "completed",
                        "result": {"availability": "confirmed", "eta": "15 min"},
                        "transcript": [{"role": "callee", "text": "Potwierdzam dostepnosc."}],
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"status": self._call_status, "detail": "call failed"}, ensure_ascii=False)
        raise KeyError(name)


def _hospital_resource() -> list[dict[str, object]]:
    return [
        {
            "resource_id": "res-hospital-1",
            "name": "Szpital Wojewodzki",
            "type": "hospital",
            "contact_phone": "+48111222333",
            "contact_role": "dyrektor-szpitala",
        }
    ]


def _road_resources() -> list[dict[str, object]]:
    return [
        {
            "resource_id": "res-police-1",
            "name": "KPP Swidnik - dyzurny ruchu",
            "type": "police",
            "contact_phone": "+48111222333",
            "contact_role": "komendant-policji",
        },
        {
            "resource_id": "res-road-1",
            "name": "GDDKiA Lublin - dyzur S17",
            "type": "road_operator",
            "contact_phone": "+48444555666",
            "contact_role": "zarzadca drogi",
        },
    ]


def test_phone_tool_starts_call_from_incident_resource_and_pauses() -> None:
    repository = FakeRepository()
    provider = CouncilPhoneToolProvider(
        repository=repository,
        mcp=FakeMCP(_hospital_resource()),
        run_id="run-1",
        incident_id="inc-1",
        agent_id="dyrektor-szpitala",
        phone_poll_interval_s=5.0,
    )

    try:
        provider.call_tool(
            PHONE_TOOL_NAME,
            {
                "unknowns": ["Czy szpital przyjmie 10 pacjentow?"],
                "preferred_contact_type": "hospital",
                "why": "Potrzebne potwierdzenie pojemnosci SOR.",
            },
        )
    except AgentToolPause as pause:
        request = pause.request
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected phone tool to pause the session")

    assert request.call_id == "call-123"
    assert request.status == "waiting"
    assert request.budget_status == "reserved"
    assert repository.get_external_info_request("run-1") is not None
    assert repository.get_external_info_request("run-1").resource_id == "res-hospital-1"
    assert repository.get_external_info_request("run-1").phone_number == "+48111222333"


def test_phone_tool_denies_second_call_while_another_call_is_still_waiting() -> None:
    repository = FakeRepository()
    repository.upsert_external_info_request(ExternalInfoRequest(
        id="ext-1",
        run_id="run-1",
        incident_id="inc-1",
        resource_id="res-hospital-1",
        resource_name="Szpital Wojewodzki",
        phone_number="+48111222333",
        owner_agent_id="dyrektor-szpitala",
        status="waiting",
        budget_status="reserved",
        notice="Trwa rozmowa z Szpital Wojewodzki.",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    ))
    mcp = FakeMCP(_hospital_resource())
    provider = CouncilPhoneToolProvider(
        repository=repository,
        mcp=mcp,
        run_id="run-1",
        incident_id="inc-1",
        agent_id="komendant-psp",
        phone_poll_interval_s=5.0,
    )

    raw = provider.call_tool(
        PHONE_TOOL_NAME,
        {
            "unknowns": ["Czy szpital przyjmie rannych?"],
            "preferred_contact_type": "hospital",
            "why": "Drugi agent probuje wykonac drugi telefon.",
        },
    )

    payload = json.loads(raw)
    assert payload["status"] == "denied_budget_exhausted"
    assert "trwa juz inne polaczenie" in payload["notice"]
    assert [name for name, _args in mcp.calls] == []


def test_phone_tool_allows_second_call_after_previous_one_completed() -> None:
    repository = FakeRepository()
    repository.upsert_external_info_request(
        ExternalInfoRequest(
            id="ext-1",
            run_id="run-1",
            incident_id="inc-1",
            resource_id="res-hospital-1",
            resource_name="Szpital Wojewodzki",
            phone_number="+48111222333",
            owner_agent_id="dyrektor-szpitala",
            call_id="call-111",
            status="completed",
            budget_status="completed",
            notice="Rozmowa zakonczona.",
            result={"availability": "confirmed"},
            created_at=datetime.now(UTC) - timedelta(minutes=2),
            updated_at=datetime.now(UTC) - timedelta(minutes=1),
            completed_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    mcp = FakeMCP(_hospital_resource())
    provider = CouncilPhoneToolProvider(
        repository=repository,
        mcp=mcp,
        run_id="run-1",
        incident_id="inc-1",
        agent_id="dyrektor-szpitala",
        phone_poll_interval_s=5.0,
    )

    try:
        provider.call_tool(
            PHONE_TOOL_NAME,
            {
                "unknowns": ["Czy szpital utrzyma dostępność SOR przez kolejne 2h?"],
                "preferred_contact_type": "hospital",
                "why": "Potrzebne kolejne potwierdzenie po zmianie sytuacji.",
            },
        )
    except AgentToolPause as pause:
        request = pause.request
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected second phone call to be allowed after previous one completed")

    assert request.call_id == "call-123"
    assert request.status == "waiting"
    assert len(repository.requests) == 2
    assert [name for name, _args in mcp.calls] == ["resource_list", "phone_agent_start_call"]


def test_phone_tool_returns_unavailable_without_incident_contact() -> None:
    repository = FakeRepository()
    mcp = FakeMCP(
        [
            {
                "resource_id": "res-no-phone",
                "name": "Operator",
                "type": "operator",
            }
        ]
    )
    provider = CouncilPhoneToolProvider(
        repository=repository,
        mcp=mcp,
        run_id="run-1",
        incident_id="inc-1",
        agent_id="dyrektor-abw",
        phone_poll_interval_s=5.0,
    )

    raw = provider.call_tool(
        PHONE_TOOL_NAME,
        {
            "unknowns": ["Czy operator potwierdza stan SCADA?"],
            "preferred_contact_type": "operator",
            "why": "Brak telefonicznego potwierdzenia operatora.",
        },
    )

    payload = json.loads(raw)
    assert payload["status"] == "unavailable_no_contact"
    assert "Brak przypisanego kontaktu" in payload["notice"]
    assert repository.requests == []
    assert [name for name, _args in mcp.calls] == ["resource_list"]


def test_phone_tool_requires_positive_match_score_before_selecting_contact() -> None:
    repository = FakeRepository()
    mcp = FakeMCP(_hospital_resource())
    provider = CouncilPhoneToolProvider(
        repository=repository,
        mcp=mcp,
        run_id="run-1",
        incident_id="inc-1",
        agent_id="komendant-policji",
        phone_poll_interval_s=5.0,
    )

    raw = provider.call_tool(
        PHONE_TOOL_NAME,
        {
            "unknowns": ["Czy DK17 i wezly S17 sa przejezdne?"],
            "preferred_contact_type": "road_operator",
            "why": "Trzeba potwierdzic objazdy i przejezdnosc.",
        },
    )

    payload = json.loads(raw)
    assert payload["status"] == "unavailable_no_contact"
    assert repository.requests == []
    assert [name for name, _args in mcp.calls] == ["resource_list"]


def test_phone_tool_prefers_road_operator_for_road_unknowns() -> None:
    repository = FakeRepository()
    provider = CouncilPhoneToolProvider(
        repository=repository,
        mcp=FakeMCP(_road_resources()),
        run_id="run-1",
        incident_id="inc-1",
        agent_id="komendant-policji",
        phone_poll_interval_s=5.0,
    )

    try:
        provider.call_tool(
            PHONE_TOOL_NAME,
            {
                "unknowns": [
                    "Ktory wezel S17 trzeba zamknac?",
                    "Czy objazd DK17 jest przejezdny dla sluzb?",
                ],
                "preferred_contact_type": "road_operator",
                "why": "Potrzebne potwierdzenie od zarzadcy drogi.",
            },
        )
    except AgentToolPause as pause:
        request = pause.request
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected phone tool to pause the session")

    assert request.resource_id == "res-road-1"
    assert request.resource_name == "GDDKiA Lublin - dyzur S17"
    assert request.status == "waiting"


def test_phone_tool_falls_back_when_multiple_resources_share_same_phone_number() -> None:
    repository = FakeRepository()
    shared_phone_resources = [
        {
            "resource_id": "res-police-1",
            "name": "KPP Swidnik - dyzurny ruchu",
            "type": "police",
            "contact_phone": "+48695031104",
            "contact_role": "komendant-policji",
        },
        {
            "resource_id": "res-road-1",
            "name": "GDDKiA Lublin - dyzur S17",
            "type": "road_operator",
            "contact_phone": "+48695031104",
            "contact_role": "zarzadca drogi",
        },
    ]
    provider = CouncilPhoneToolProvider(
        repository=repository,
        mcp=FakeMCP(shared_phone_resources),
        run_id="run-1",
        incident_id="inc-1",
        agent_id="dyrektor-abw",
        phone_poll_interval_s=5.0,
    )

    try:
        provider.call_tool(
            PHONE_TOOL_NAME,
            {
                "unknowns": ["Potwierdz aktualny stan zdarzenia."],
                "preferred_contact_type": "unknown_dispatch",
                "why": "W demo wszystkie kontakty prowadza na ten sam numer.",
            },
        )
    except AgentToolPause as pause:
        request = pause.request
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected phone tool to pause the session")

    assert request.phone_number == "+48695031104"
    assert request.resource_id in {"res-police-1", "res-road-1"}
    assert request.status == "waiting"


def test_poll_external_info_request_completes_and_writes_incident_update() -> None:
    repository = FakeRepository()
    request = ExternalInfoRequest(
        id="ext-1",
        run_id="run-1",
        incident_id="inc-1",
        resource_id="res-hospital-1",
        resource_name="Szpital Wojewodzki",
        phone_number="+48111222333",
        owner_agent_id="dyrektor-szpitala",
        call_id="call-123",
        unknowns=["Czy szpital przyjmie 10 pacjentow?"],
        status="waiting",
        budget_status="reserved",
        notice="Trwa rozmowa z Szpital Wojewodzki.",
        created_at=datetime.now(UTC) - timedelta(seconds=5),
        updated_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    repository.upsert_external_info_request(request)

    updated_request, tool_payload = poll_external_info_request(
        repository=repository,
        mcp=FakeMCP(_hospital_resource()),
        request=request,
        phone_max_wait_s=60.0,
    )

    payload = json.loads(tool_payload or "{}")
    assert updated_request.status == "completed"
    assert updated_request.budget_status == "completed"
    assert payload["status"] == "completed"
    assert payload["result"]["availability"] == "confirmed"
    assert repository.updates
    assert repository.updates[0][3] == "phone_verification"


def test_poll_external_info_request_accepts_naive_created_at() -> None:
    repository = FakeRepository()
    request = ExternalInfoRequest(
        id="ext-1",
        run_id="run-1",
        incident_id="inc-1",
        resource_id="res-hospital-1",
        resource_name="Szpital Wojewodzki",
        phone_number="+48111222333",
        owner_agent_id="dyrektor-szpitala",
        call_id="call-123",
        unknowns=["Czy szpital przyjmie 10 pacjentow?"],
        status="waiting",
        budget_status="reserved",
        notice="Trwa rozmowa z Szpital Wojewodzki.",
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    repository.upsert_external_info_request(request)

    updated_request, tool_payload = poll_external_info_request(
        repository=repository,
        mcp=FakeMCP(_hospital_resource()),
        request=request,
        phone_max_wait_s=60.0,
    )

    payload = json.loads(tool_payload or "{}")
    assert updated_request.status == "completed"
    assert payload["status"] == "completed"
