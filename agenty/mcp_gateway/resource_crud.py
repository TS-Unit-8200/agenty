"""MCP adapter that proxies resource CRUD calls to Next.js API."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agenty.orchestration.tracing import trace_event


class ResourceCrudMCPServer:
    def __init__(self, api_base_url: str, api_token: str | None = None, timeout_s: float = 120.0) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._api_token = api_token
        self._timeout_s = timeout_s

    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [
            {"name": "resource_list", "description": "List resources for incident", "input_schema": {"type": "object", "properties": {"incident_id": {"type": "string"}}, "required": ["incident_id"]}},
            {"name": "resource_create", "description": "Create a resource assignment", "input_schema": {"type": "object", "properties": {"incident_id": {"type": "string"}, "name": {"type": "string"}, "type": {"type": "string"}, "status": {"type": "string"}}, "required": ["incident_id", "name", "type", "status"]}},
            {"name": "resource_update_status", "description": "Update resource status", "input_schema": {"type": "object", "properties": {"resource_id": {"type": "string"}, "status": {"type": "string"}}, "required": ["resource_id", "status"]}},
            {"name": "resource_release", "description": "Release assigned resource", "input_schema": {"type": "object", "properties": {"resource_id": {"type": "string"}}, "required": ["resource_id"]}},
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "resource_list":
            query = urlencode({"incident_id": str(arguments["incident_id"])})
            return self._request("GET", f"/api/resources?{query}")
        if name == "resource_create":
            return self._request("POST", "/api/resources", payload=arguments)
        if name == "resource_update_status":
            resource_id = str(arguments["resource_id"])
            payload = {"status": arguments["status"]}
            return self._request("PATCH", f"/api/resources/{resource_id}", payload=payload)
        if name == "resource_release":
            resource_id = str(arguments["resource_id"])
            payload = {"status": "released"}
            return self._request("PATCH", f"/api/resources/{resource_id}", payload=payload)
        raise KeyError(f"Unsupported tool: {name}")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> str:
        trace_event("mcp.resource.request", method=method, path=path)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        req = Request(
            url=f"{self._api_base_url}{path}",
            data=body,
            method=method,
            headers=self._headers(payload is not None),
        )
        try:
            with urlopen(req, timeout=self._timeout_s) as response:
                result = response.read().decode("utf-8")
                trace_event("mcp.resource.response", method=method, path=path)
                return result
        except TimeoutError:
            trace_event("mcp.resource.error", method=method, path=path, error="timeout")
            # sync_resources expects JSON list from resource_list; empty keeps the graph alive.
            if method == "GET" and path.startswith("/api/resources"):
                return "[]"
            return json.dumps({"ok": False, "error": "timeout"})
        except HTTPError as exc:
            trace_event("mcp.resource.error", method=method, path=path, error=str(exc))
            if method == "GET" and path.startswith("/api/resources") and exc.code == 404:
                return "[]"
            return json.dumps({"ok": False, "error": f"HTTP {exc.code}", "detail": exc.reason})
        except URLError as exc:
            trace_event("mcp.resource.error", method=method, path=path, error=str(exc))
            if method == "GET" and path.startswith("/api/resources"):
                return "[]"
            return json.dumps({"ok": False, "error": "ConnectionError", "detail": str(exc.reason)})

    def _headers(self, has_body: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if has_body:
            headers["Content-Type"] = "application/json"
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers
