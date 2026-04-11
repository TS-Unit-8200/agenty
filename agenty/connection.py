"""OpenAI-compatible client for Comtegra GPU Cloud LLM API."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from agenty.config import Settings


class LlmConnection:
    """Thin wrapper around the OpenAI SDK pointed at CGC (or any compatible base URL)."""

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
        **kwargs: Any,
    ) -> str:
        """Run a single chat completion; returns assistant text (empty string if none)."""
        response = self._client.chat.completions.create(
            model=model or self._default_model,
            messages=messages,
            **kwargs,
        )
        choice = response.choices[0].message
        return (choice.content or "").strip()

    def raw_client(self) -> OpenAI:
        """Escape hatch for embeddings, streaming, or endpoints not wrapped here."""
        return self._client
