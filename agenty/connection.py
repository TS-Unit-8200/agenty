"""OpenAI-compatible client (Claude via Anthropic or Comtegra GPU Cloud)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from agenty.config import Settings


def _stringify_content_fragment(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict):
                ptype = part.get("type")
                if ptype == "text":
                    chunks.append(str(part.get("text", "")))
                elif ptype in ("reasoning", "thinking"):
                    chunks.append(str(part.get("text") or part.get("content", "")))
                else:
                    chunks.append(json.dumps(part, ensure_ascii=False))
            else:
                chunks.append(str(part))
        return "\n".join(chunks).strip()
    return str(content).strip()


def _split_reasoning_from_message(message: Any) -> tuple[str, str | None]:
    """Return (answer, reasoning) from a ChatCompletionMessage (provider-specific fields)."""
    raw = getattr(message, "content", None)
    reasoning: str | None = None
    answer: str

    if isinstance(raw, list):
        reasoning_chunks: list[str] = []
        answer_chunks: list[str] = []
        for part in raw:
            if not isinstance(part, dict):
                answer_chunks.append(str(part))
                continue
            ptype = part.get("type")
            if ptype in ("reasoning", "thinking"):
                reasoning_chunks.append(str(part.get("text") or part.get("content") or ""))
            elif ptype == "text":
                answer_chunks.append(str(part.get("text", "")))
            else:
                answer_chunks.append(json.dumps(part, ensure_ascii=False))
        answer = "\n".join(answer_chunks).strip()
        r = "\n".join(reasoning_chunks).strip()
        reasoning = r or None
    else:
        answer = _stringify_content_fragment(raw)

    if reasoning is None:
        for attr in ("reasoning_content", "reasoning"):
            val = getattr(message, attr, None)
            if isinstance(val, str) and val.strip():
                reasoning = val.strip()
                break
    if reasoning is None:
        extra = getattr(message, "model_extra", None) or {}
        if isinstance(extra, dict):
            for key in ("reasoning_content", "reasoning", "thinking"):
                val = extra.get(key)
                if isinstance(val, str) and val.strip():
                    reasoning = val.strip()
                    break

    refusal = getattr(message, "refusal", None)
    if isinstance(refusal, str) and refusal.strip():
        answer = f"[refusal] {refusal.strip()}\n\n{answer}".strip()

    return answer, reasoning


@dataclass
class ChatToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


@dataclass
class ChatTurn:
    answer: str
    reasoning: str | None
    tool_calls: list[ChatToolCall]
    assistant_message: dict[str, Any]


class LlmConnection:
    """Thin wrapper around the OpenAI SDK (Anthropic Claude or CGC / any compatible base URL)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._clients: dict[tuple[str, str], OpenAI] = {}
        self._default_model = settings.default_chat_model

    @property
    def default_model(self) -> str:
        return self._default_model

    def _client_for(self, *, execution_mode: str | None = None) -> tuple[OpenAI, str]:
        base_url, api_key, model = self._settings.resolve_llm_profile(execution_mode)
        key = (base_url, api_key)
        client = self._clients.get(key)
        if client is None:
            client = OpenAI(base_url=base_url, api_key=api_key)
            self._clients[key] = client
        return client, model

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        execution_mode: str | None = None,
        log_label: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Run a single chat completion; returns assistant text (empty string if none)."""
        kwargs = dict(kwargs)
        log_label = kwargs.pop("log_label", log_label)
        client, default_model = self._client_for(execution_mode=execution_mode)

        response = client.chat.completions.create(
            model=model or default_model,
            messages=messages,
            **kwargs,
        )
        choice = response.choices[0].message
        answer, reasoning = _split_reasoning_from_message(choice)
        model_used = getattr(response, "model", None) or model or default_model
        title = log_label or "llm.chat_completion"
        try:
            from agenty.orchestration.tracing import trace_llm_output

            trace_llm_output(
                title=title,
                answer=answer,
                reasoning=reasoning,
                model=model_used,
            )
        except Exception:  # noqa: BLE001 — never break completion if logging fails
            pass
        return answer

    def chat_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        model: str | None = None,
        execution_mode: str | None = None,
        log_label: str | None = None,
        **kwargs: Any,
    ) -> ChatTurn:
        kwargs = dict(kwargs)
        log_label = kwargs.pop("log_label", log_label)
        client, default_model = self._client_for(execution_mode=execution_mode)
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice

        response = client.chat.completions.create(
            model=model or default_model,
            messages=messages,
            **kwargs,
        )
        choice = response.choices[0].message
        answer, reasoning = _split_reasoning_from_message(choice)
        raw_tool_calls = getattr(choice, "tool_calls", None) or []
        tool_calls: list[ChatToolCall] = []
        assistant_tool_calls: list[dict[str, Any]] = []
        for item in raw_tool_calls:
            function = getattr(item, "function", None)
            name = str(getattr(function, "name", "") or "")
            raw_arguments = str(getattr(function, "arguments", "") or "{}")
            try:
                parsed = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError:
                parsed = {}
            tool_call_id = str(getattr(item, "id", "") or "")
            tool_calls.append(
                ChatToolCall(
                    id=tool_call_id,
                    name=name,
                    arguments=parsed if isinstance(parsed, dict) else {},
                    raw_arguments=raw_arguments,
                )
            )
            assistant_tool_calls.append(
                {
                    "id": tool_call_id,
                    "type": str(getattr(item, "type", "function") or "function"),
                    "function": {
                        "name": name,
                        "arguments": raw_arguments,
                    },
                }
            )

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": getattr(choice, "content", None),
        }
        if assistant_tool_calls:
            assistant_message["tool_calls"] = assistant_tool_calls

        model_used = getattr(response, "model", None) or model or default_model
        title = log_label or "llm.chat_turn"
        try:
            from agenty.orchestration.tracing import trace_llm_output

            trace_llm_output(
                title=title,
                answer=answer,
                reasoning=reasoning,
                model=model_used,
            )
        except Exception:  # noqa: BLE001
            pass

        return ChatTurn(
            answer=answer,
            reasoning=reasoning,
            tool_calls=tool_calls,
            assistant_message=assistant_message,
        )

    def raw_client(self) -> OpenAI:
        """Escape hatch for embeddings, streaming, or endpoints not wrapped here."""
        client, _ = self._client_for()
        return client
