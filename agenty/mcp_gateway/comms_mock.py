"""Mock communications adapter matching the real phone-agent MCP contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agenty.connection import LlmConnection
from agenty.orchestration.tracing import trace_event


class CommsMockMCPServer:
    def __init__(self, llm: LlmConnection | None = None) -> None:
        self._llm = llm
        self._calls: dict[str, dict[str, Any]] = {}

    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "phone_agent_start_call",
                "description": "Mock outbound phone call collecting structured data from a resource contact.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone_number": {"type": "string"},
                        "schema": {"type": "object"},
                        "requirements": {"type": "string"},
                        "resource_name": {"type": "string"},
                    },
                    "required": ["phone_number", "schema", "requirements"],
                },
            },
            {
                "name": "phone_agent_get_call",
                "description": "Return a previously mocked call result.",
                "input_schema": {
                    "type": "object",
                    "properties": {"call_id": {"type": "string"}},
                    "required": ["call_id"],
                },
            },
            {
                "name": "call_user_for_incident_info",
                "description": "Backward-compatible legacy mock call summary.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "incident_id": {"type": "string"},
                        "phone": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                    "required": ["incident_id", "summary"],
                },
            },
            {
                "name": "get_call_summary",
                "description": "Backward-compatible legacy mock call summary lookup.",
                "input_schema": {
                    "type": "object",
                    "properties": {"incident_id": {"type": "string"}},
                    "required": ["incident_id"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        trace_event("mcp.comms.request", tool=name)
        if name == "phone_agent_start_call":
            return self._start_call(arguments)
        if name == "phone_agent_get_call":
            return self._get_call(arguments)
        if name == "call_user_for_incident_info":
            return self._legacy_summary(arguments)
        if name == "get_call_summary":
            return json.dumps(
                {
                    "incident_id": arguments["incident_id"],
                    "summary": "No persisted legacy call state in mock mode.",
                }
            )
        raise KeyError(f"Unsupported tool: {name}")

    def _start_call(self, arguments: dict[str, Any]) -> str:
        call_id = f"mock-{uuid4()}"
        phone_number = str(arguments.get("phone_number", "unknown"))
        requirements = str(arguments.get("requirements", ""))
        schema_def = arguments.get("schema") or {}
        resource_name = str(arguments.get("resource_name", "Zasob"))
        created_at = datetime.now(UTC).isoformat()
        result = self._mock_result(resource_name=resource_name, requirements=requirements, schema_def=schema_def)
        self._calls[call_id] = {
            "call_id": call_id,
            "status": "completed",
            "result": result,
            "transcript": [
                {
                    "role": "assistant",
                    "text": f"Kontaktujemy sie z {resource_name}.",
                    "done": False,
                },
                {
                    "role": "user",
                    "text": f"Potwierdzenie telefoniczne dla {phone_number}: {requirements}",
                    "done": True,
                },
            ],
            "created_at": created_at,
            "updated_at": created_at,
        }
        trace_event("mcp.comms.response", tool="phone_agent_start_call", call_id=call_id)
        return json.dumps({"call_id": call_id, "status": "initiated"})

    def _get_call(self, arguments: dict[str, Any]) -> str:
        call_id = str(arguments["call_id"])
        payload = self._calls.get(call_id)
        if payload is None:
            return json.dumps({"call_id": call_id, "status": "failed", "result": None, "transcript": []})
        trace_event("mcp.comms.response", tool="phone_agent_get_call", call_id=call_id)
        return json.dumps(payload)

    def _legacy_summary(self, arguments: dict[str, Any]) -> str:
        incident_id = str(arguments["incident_id"])
        summary = str(arguments["summary"])
        phone = str(arguments.get("phone", "unknown"))
        if self._llm is None:
            return json.dumps(
                {
                    "incident_id": incident_id,
                    "phone": phone,
                    "summary": f"[MOCK CALL] Caller confirmed incident details: {summary}",
                    "extracted_facts": [
                        "Caller confirmed incident is active.",
                        "Additional details should be validated by field teams.",
                    ],
                }
            )
        prompt = (
            "Generate a short mock phone call summary with 2-4 extracted facts. "
            "Keep it factual and concise."
        )
        llm_output = self._llm.chat_completion(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"Incident ID: {incident_id}\nPhone: {phone}\nKnown summary: {summary}",
                },
            ],
            log_label="mcp:comms_mock_call",
        )
        return json.dumps({"incident_id": incident_id, "phone": phone, "summary": llm_output})

    def _mock_result(
        self,
        *,
        resource_name: str,
        requirements: str,
        schema_def: dict[str, Any],
    ) -> dict[str, Any]:
        keys = list((schema_def.get("properties") or {}).keys())
        if not keys:
            keys = ["availability", "capacity", "constraints"]
        result: dict[str, Any] = {
            keys[0]: f"{resource_name} potwierdza gotowosc do wspolpracy.",
        }
        if len(keys) > 1:
            result[keys[1]] = "Dostepnosc potwierdzona telefonicznie."
        if len(keys) > 2:
            result[keys[2]] = requirements or "Brak dodatkowych ograniczen."
        return result
