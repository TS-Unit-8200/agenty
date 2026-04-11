"""Mocked communications MCP adapter (Twilio-compatible contract shape)."""

from __future__ import annotations

import json
from typing import Any

from agenty.connection import LlmConnection
from agenty.orchestration.tracing import trace_event


class CommsMockMCPServer:
    def __init__(self, llm: LlmConnection | None = None) -> None:
        self._llm = llm

    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [
            {"name": "call_user_for_incident_info", "description": "Mock call to collect additional incident details", "input_schema": {"type": "object", "properties": {"incident_id": {"type": "string"}, "phone": {"type": "string"}, "summary": {"type": "string"}}, "required": ["incident_id", "summary"]}},
            {"name": "get_call_summary", "description": "Return previously generated mock call summary", "input_schema": {"type": "object", "properties": {"incident_id": {"type": "string"}}, "required": ["incident_id"]}},
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        trace_event("mcp.comms.request", tool=name, incident_id=arguments.get("incident_id"))
        if name == "call_user_for_incident_info":
            result = self._generate_summary(arguments)
            trace_event("mcp.comms.response", tool=name, incident_id=arguments.get("incident_id"))
            return result
        if name == "get_call_summary":
            result = json.dumps(
                {
                    "incident_id": arguments["incident_id"],
                    "summary": "No persisted call state in mock mode. Use call_user_for_incident_info in the same workflow.",
                }
            )
            trace_event("mcp.comms.response", tool=name, incident_id=arguments.get("incident_id"))
            return result
        raise KeyError(f"Unsupported tool: {name}")

    def _generate_summary(self, arguments: dict[str, Any]) -> str:
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
        return json.dumps(
            {
                "incident_id": incident_id,
                "phone": phone,
                "summary": llm_output,
            }
        )
