"""Structured context passed into an agent session (incident facts, attachments, etc.)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentContext:
    """Runtime context merged into the system prompt when a session starts."""

    preamble: str | None = None
    sections: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        """Human-readable block appended after base agent instructions."""
        parts: list[str] = []
        if self.preamble:
            parts.append(self.preamble.strip())
        for title, body in self.sections.items():
            body = body.strip()
            if not body:
                continue
            parts.append(f"## {title}\n{body}")
        return "\n\n".join(parts).strip()

    def is_empty(self) -> bool:
        return not self.preamble and not self.sections and not self.metadata
