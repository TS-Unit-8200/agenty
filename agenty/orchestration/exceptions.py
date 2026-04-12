"""Workflow control-flow exceptions."""

from __future__ import annotations

from typing import Any


class WorkflowPause(RuntimeError):
    """Signal that the workflow should pause and later resume the same run."""

    def __init__(self, *, reason: str, delay_s: float) -> None:
        super().__init__(reason)
        self.reason = reason
        self.delay_s = delay_s


class AgentToolPause(RuntimeError):
    """Signal that a tool started an external action and the agent session must pause."""

    def __init__(self, *, request: Any, delay_s: float) -> None:
        super().__init__(getattr(request, "notice", None) or "agent tool paused")
        self.request = request
        self.delay_s = delay_s


class AgentSessionPause(RuntimeError):
    """Signal that the agent session paused after emitting a tool call."""

    def __init__(
        self,
        *,
        request: Any,
        delay_s: float,
        tool_call_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        super().__init__(getattr(request, "notice", None) or "agent session paused")
        self.request = request
        self.delay_s = delay_s
        self.tool_call_id = tool_call_id
        self.messages = messages
