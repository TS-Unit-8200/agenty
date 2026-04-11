"""Agent definitions, registry, runtime, and chat sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from agenty.config import Settings, get_settings
from agenty.connection import LlmConnection
from agenty.context import AgentContext
from agenty.mcp import MCPProvider


@dataclass(frozen=True)
class AgentDefinition:
    """Static description of an agent (usually loaded from ``agents/*.md``)."""

    agent_id: str
    title: str
    instructions: str
    source_path: Path | None = None


class AgentRegistry:
    """Maps agent ids to definitions by scanning markdown instruction files."""

    def __init__(self, agents_dir: Path | None = None) -> None:
        root = Path(__file__).resolve().parent.parent
        self.agents_dir = agents_dir or (root / "agents")
        self._by_id: dict[str, AgentDefinition] = {}
        self.reload()

    def reload(self) -> None:
        self._by_id.clear()
        if not self.agents_dir.is_dir():
            return
        for path in sorted(self.agents_dir.glob("*.md")):
            definition = self._load_file(path)
            self._by_id[definition.agent_id] = definition

    def _load_file(self, path: Path) -> AgentDefinition:
        text = path.read_text(encoding="utf-8")
        agent_id = path.stem
        title = _parse_title(text) or agent_id.replace("-", " ").title()
        return AgentDefinition(
            agent_id=agent_id,
            title=title,
            instructions=text.strip(),
            source_path=path,
        )

    def get(self, agent_id: str) -> AgentDefinition:
        if agent_id not in self._by_id:
            available = ", ".join(sorted(self._by_id)) or "(none)"
            msg = f"Unknown agent_id={agent_id!r}. Loaded: {available}"
            raise KeyError(msg)
        return self._by_id[agent_id]

    def list_ids(self) -> list[str]:
        return sorted(self._by_id)


def _parse_title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Agent:"):
            return stripped.removeprefix("# Agent:").strip()
        if stripped.startswith("# "):
            return stripped.removeprefix("# ").strip()
    return None


class AgentSession:
    """One running conversation: base instructions + optional context + turns."""

    def __init__(
        self,
        *,
        definition: AgentDefinition,
        llm: LlmConnection,
        context: AgentContext | None = None,
        model: str | None = None,
        mcp: MCPProvider | None = None,
    ) -> None:
        self._definition = definition
        self._llm = llm
        self._model = model
        self._mcp = mcp
        self._messages: list[dict[str, Any]] = []
        self._bootstrap_system(context)

    @property
    def definition(self) -> AgentDefinition:
        return self._definition

    @property
    def messages(self) -> Sequence[dict[str, Any]]:
        return tuple(self._messages)

    def _bootstrap_system(self, context: AgentContext | None) -> None:
        parts: list[str] = [self._definition.instructions]
        if context and not context.is_empty():
            rendered = context.render()
            if rendered:
                parts.append("# Current context\n" + rendered)
        system_content = "\n\n".join(parts)
        self._messages.append({"role": "system", "content": system_content})

    def say(self, user_message: str, **completion_kwargs: Any) -> str:
        """Append a user turn, call the model, append assistant reply; return assistant text."""
        self._messages.append({"role": "user", "content": user_message})
        # MCP tool loop can be added later by intercepting tool_calls on the message.
        _ = self._mcp  # reserved for tool-use orchestration
        reply = self._llm.chat_completion(
            list(self._messages),
            model=self._model,
            log_label=f"agent:{self._definition.agent_id}",
            **completion_kwargs,
        )
        self._messages.append({"role": "assistant", "content": reply})
        return reply


class AgentRuntime:
    """Factory for ``AgentSession`` instances using a shared LLM connection and registry."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        registry: AgentRegistry | None = None,
        llm: LlmConnection | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self.registry = registry or AgentRegistry()
        self.llm = llm or LlmConnection(self._settings)

    def start(
        self,
        agent_id: str,
        *,
        context: AgentContext | None = None,
        model: str | None = None,
        mcp: MCPProvider | None = None,
    ) -> AgentSession:
        """Start a new session for the given agent definition."""
        definition = self.registry.get(agent_id)
        return AgentSession(
            definition=definition,
            llm=self.llm,
            context=context,
            model=model,
            mcp=mcp,
        )
