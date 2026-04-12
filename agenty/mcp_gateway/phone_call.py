"""MCP adapter for the real ai-backend phone-call service."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agenty.orchestration.tracing import trace_event


class PhoneCallMCPServer:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_s: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._timeout_s = timeout_s

    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "phone_agent_start_call",
                "description": "Create an outbound ai-backend phone call and return call_id.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone_number": {"type": "string"},
                        "schema": {"type": "object"},
                        "requirements": {"type": "string"},
                    },
                    "required": ["phone_number", "schema", "requirements"],
                },
            },
            {
                "name": "phone_agent_get_call",
                "description": "Poll ai-backend for call status and extracted result.",
                "input_schema": {
                    "type": "object",
                    "properties": {"call_id": {"type": "string"}},
                    "required": ["call_id"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "phone_agent_start_call":
            return self._request("POST", "/calls", payload=arguments)
        if name == "phone_agent_get_call":
            call_id = str(arguments["call_id"])
            return self._request("GET", f"/calls/{call_id}")
        raise KeyError(f"Unsupported tool: {name}")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> str:
        trace_event("mcp.phone.request", method=method, path=path)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url=f"{self._base_url}{path}",
            data=body,
            method=method,
            headers=self._headers(has_body=payload is not None),
        )
        try:
            with urlopen(request, timeout=self._timeout_s) as response:
                result = response.read().decode("utf-8")
                trace_event("mcp.phone.response", method=method, path=path)
                return result
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            trace_event("mcp.phone.error", method=method, path=path, error=f"HTTP {exc.code}: {detail}")
            return json.dumps(
                {
                    "ok": False,
                    "error": f"HTTP {exc.code}",
                    "detail": detail or exc.reason,
                }
            )
        except URLError as exc:
            trace_event("mcp.phone.error", method=method, path=path, error=str(exc.reason))
            return json.dumps({"ok": False, "error": "ConnectionError", "detail": str(exc.reason)})
        except TimeoutError:
            trace_event("mcp.phone.error", method=method, path=path, error="timeout")
            return json.dumps({"ok": False, "error": "timeout"})

    def _headers(self, *, has_body: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_token}",
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers
