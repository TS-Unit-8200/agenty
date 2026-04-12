"""Agent definitions, registry, runtime, and chat sessions."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Sequence

from agenty.connection import ChatTurn, LlmConnection
from agenty.config import Settings, get_settings
from agenty.context import AgentContext
from agenty.mcp import MCPProvider
from agenty.orchestration.exceptions import AgentSessionPause, AgentToolPause


@dataclass(frozen=True)
class AgentDefinition:
    """Static description of an agent (usually loaded from ``agents/*.md``)."""

    agent_id: str
    title: str
    instructions: str
    source_path: Path | None = None


@dataclass(frozen=True)
class AgentToolEvent:
    name: str
    status: str
    notice: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None


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

    _MAX_TOOL_ROUNDS = 4

    def __init__(
        self,
        *,
        definition: AgentDefinition,
        llm: LlmConnection,
        context: AgentContext | None = None,
        model: str | None = None,
        execution_mode: str | None = None,
        mcp: MCPProvider | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        self._definition = definition
        self._llm = llm
        self._model = model
        self._execution_mode = execution_mode
        self._mcp = mcp
        self._messages: list[dict[str, Any]] = [dict(item) for item in messages] if messages else []
        self._last_tool_event: AgentToolEvent | None = None
        if not self._messages:
            self._bootstrap_system(context)

    @property
    def definition(self) -> AgentDefinition:
        return self._definition

    @property
    def messages(self) -> Sequence[dict[str, Any]]:
        return tuple(self._messages)

    @property
    def last_tool_event(self) -> AgentToolEvent | None:
        return self._last_tool_event

    def _bootstrap_system(self, context: AgentContext | None) -> None:
        parts: list[str] = [self._definition.instructions]
        if context and not context.is_empty():
            rendered = context.render()
            if rendered:
                parts.append("# Current context\n" + rendered)
        system_content = "\n\n".join(parts)
        self._messages.append({"role": "system", "content": system_content})

    def _tool_specs_for_chat(self) -> list[dict[str, Any]]:
        if self._mcp is None:
            return []
        specs = self._mcp.list_tool_specs()
        tools: list[dict[str, Any]] = []
        for spec in specs:
            name = str(spec.get("name", "") or "").strip()
            if not name:
                continue
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(spec.get("description", "") or ""),
                        "parameters": spec.get("input_schema") or {"type": "object", "properties": {}},
                    },
                }
            )
        return tools

    def _record_tool_result(self, tool_name: str, payload: Any) -> None:
        parsed: dict[str, Any] | None = None
        if isinstance(payload, dict):
            parsed = payload
        elif isinstance(payload, str):
            try:
                candidate = json.loads(payload)
            except json.JSONDecodeError:
                candidate = None
            if isinstance(candidate, dict):
                parsed = candidate
        if not parsed:
            return

        status = str(parsed.get("status") or "").strip()
        if not status:
            return

        notice = str(parsed.get("notice") or parsed.get("reason") or "").strip() or None
        resource_id = str(parsed.get("resource_id") or "").strip() or None
        resource_name = str(parsed.get("resource_name") or "").strip() or None
        next_event = AgentToolEvent(
            name=tool_name,
            status=status,
            notice=notice,
            resource_id=resource_id,
            resource_name=resource_name,
        )
        precedence = {
            "idle": 0,
            "unavailable_no_contact": 1,
            "denied_budget_exhausted": 1,
            "failed": 2,
            "timed_out": 2,
            "completed": 3,
        }
        if self._last_tool_event is not None:
            current_score = precedence.get(self._last_tool_event.status, 0)
            next_score = precedence.get(next_event.status, 0)
            if current_score >= next_score:
                return
        self._last_tool_event = next_event

    def _handle_tool_calls(self, turn: ChatTurn) -> str | None:
        if not turn.tool_calls:
            return turn.answer

        self._messages.append(turn.assistant_message)
        for tool_call in turn.tool_calls:
            try:
                tool_result = self._mcp.call_tool(tool_call.name, tool_call.arguments) if self._mcp else "{}"
            except AgentToolPause as pause:
                raise AgentSessionPause(
                    request=pause.request,
                    delay_s=pause.delay_s,
                    tool_call_id=tool_call.id,
                    messages=list(self._messages),
                ) from pause
            self._record_tool_result(tool_call.name, tool_result)
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )
        return None

    def _run_with_tools(self, **completion_kwargs: Any) -> str:
        tools = self._tool_specs_for_chat()
        if not tools:
            reply = self._llm.chat_completion(
                list(self._messages),
                model=self._model,
                execution_mode=self._execution_mode,
                log_label=f"agent:{self._definition.agent_id}",
                **completion_kwargs,
            )
            self._messages.append({"role": "assistant", "content": reply})
            return reply

        for _ in range(self._MAX_TOOL_ROUNDS):
            turn = self._llm.chat_turn(
                list(self._messages),
                model=self._model,
                execution_mode=self._execution_mode,
                tools=tools,
                tool_choice="auto",
                log_label=f"agent:{self._definition.agent_id}",
                **completion_kwargs,
            )
            maybe_answer = self._handle_tool_calls(turn)
            if maybe_answer is not None:
                self._messages.append({"role": "assistant", "content": maybe_answer})
                return maybe_answer

        raise RuntimeError(f"Agent {self._definition.agent_id} exceeded tool loop limit")

    def say(self, user_message: str, **completion_kwargs: Any) -> str:
        """Append a user turn, call the model, append assistant reply; return assistant text."""
        self._messages.append({"role": "user", "content": user_message})
        return self._run_with_tools(**completion_kwargs)

    def resume_after_tool(
        self,
        tool_call_id: str,
        tool_payload: dict[str, Any],
        *,
        tool_name: str | None = None,
        **completion_kwargs: Any,
    ) -> str:
        self._record_tool_result(tool_name or "tool", tool_payload)
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(tool_payload, ensure_ascii=False),
            }
        )
        return self._run_with_tools(**completion_kwargs)


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
        execution_mode: str | None = None,
        mcp: MCPProvider | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> AgentSession:
        """Start a new session for the given agent definition."""
        definition = self.registry.get(agent_id)
        return AgentSession(
            definition=definition,
            llm=self.llm,
            context=context,
            model=model,
            execution_mode=execution_mode,
            mcp=mcp,
            messages=messages,
        )
