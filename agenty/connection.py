"""OpenAI-compatible client (Claude via Anthropic or Comtegra GPU Cloud)."""

from __future__ import annotations

import json
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


class LlmConnection:
    """Thin wrapper around the OpenAI SDK (Anthropic Claude or CGC / any compatible base URL)."""

    def __init__(self, settings: Settings) -> None:
        base = settings.llm_base_url.rstrip("/")
        self._client = OpenAI(base_url=base, api_key=settings.llm_api_key)
        self._default_model = settings.default_chat_model

    @property
    def default_model(self) -> str:
        return self._default_model

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        log_label: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Run a single chat completion; returns assistant text (empty string if none)."""
        kwargs = dict(kwargs)
        log_label = kwargs.pop("log_label", log_label)

        response = self._client.chat.completions.create(
            model=model or self._default_model,
            messages=messages,
            **kwargs,
        )
        choice = response.choices[0].message
        answer, reasoning = _split_reasoning_from_message(choice)
        model_used = getattr(response, "model", None) or model or self._default_model
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

    def raw_client(self) -> OpenAI:
        """Escape hatch for embeddings, streaming, or endpoints not wrapped here."""
        return self._client
