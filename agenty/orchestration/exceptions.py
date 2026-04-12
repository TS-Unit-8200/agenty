"""Workflow control-flow exceptions."""

from __future__ import annotations


class WorkflowPause(RuntimeError):
    """Signal that the workflow should pause and later resume the same run."""

    def __init__(self, *, reason: str, delay_s: float) -> None:
        super().__init__(reason)
        self.reason = reason
        self.delay_s = delay_s
