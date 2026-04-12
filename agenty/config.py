"""Application settings (Claude via Anthropic or CGC — OpenAI-compatible chat API)."""

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: repository ``agenty/`` folder (parent of the ``agenty`` Python package).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Environment-backed configuration.

    Set ``ANTHROPIC_API_KEY`` to use Claude (OpenAI SDK compatibility), or ``CGC_LLM_*`` for
    Comtegra GPU Cloud: https://docs.cgc.comtegra.cloud/llm-api
    """

    model_config = SettingsConfigDict(
        env_file=_DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_base_url: str = Field(
        default="https://llm.comtegra.cloud/v1",
        validation_alias="CGC_LLM_BASE_URL",
    )
    llm_api_key: str = Field(default="", validation_alias="CGC_LLM_API_KEY")
    default_chat_model: str = Field(
        default="llama3-8b",
        validation_alias="CGC_LLM_CHAT_MODEL",
    )
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Claude API key; when set, overrides CGC LLM base URL, key, and default model.",
    )
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com/v1",
        validation_alias="ANTHROPIC_BASE_URL",
        description="OpenAI-compatible Claude API root (trailing slash optional).",
    )
    anthropic_chat_model: str = Field(
        default="claude-sonnet-4-6",
        validation_alias="ANTHROPIC_CHAT_MODEL",
        description="Model id when using ANTHROPIC_API_KEY.",
    )
    database_url: str | None = Field(
        default=None,
        validation_alias="DATABASE_URL",
        description="MongoDB connection URI (e.g. Atlas mongodb+srv://…).",
    )
    mongodb_database: str | None = Field(
        default=None,
        validation_alias="MONGODB_DATABASE",
        description="Database name when the URI has no path segment.",
    )
    nextjs_api_base_url: str = Field(
        default="http://localhost:3000",
        validation_alias="NEXTJS_API_BASE_URL",
        description="Next.js backend base URL for resource CRUD proxy calls.",
    )
    nextjs_api_token: str | None = Field(
        default=None,
        validation_alias="NEXTJS_API_TOKEN",
        description="Optional bearer token for service-to-service calls to Next.js.",
    )
    nextjs_http_timeout_s: float = Field(
        default=120.0,
        validation_alias="NEXTJS_HTTP_TIMEOUT_S",
        description="Timeout for agenty → Next.js MCP resource HTTP calls (resource_list, etc.).",
    )
    phone_agent_base_url: str = Field(
        default="http://localhost:5000",
        validation_alias="PHONE_AGENT_BASE_URL",
        description="Base URL of the ai-backend phone agent API.",
    )
    phone_agent_api_token: str | None = Field(
        default=None,
        validation_alias="PHONE_AGENT_API_TOKEN",
        description="Bearer token used to authenticate against ai-backend /calls endpoints.",
    )
    phone_agent_default_phone_number: str | None = Field(
        default=None,
        validation_alias="PHONE_AGENT_DEFAULT_PHONE_NUMBER",
        description="Fallback destination phone number when the incident has no contact resources but the council exposes explicit unknowns.",
    )
    phone_agent_poll_interval_s: float = Field(
        default=10.0,
        validation_alias="PHONE_AGENT_POLL_INTERVAL_S",
        description="Polling interval for waiting phone calls before resuming a workflow run.",
    )
    phone_agent_max_wait_s: float = Field(
        default=600.0,
        validation_alias="PHONE_AGENT_MAX_WAIT_S",
        description="Maximum time a workflow run should wait for ai-backend call completion.",
    )
    phone_agent_enabled: bool = Field(
        default=False,
        validation_alias="PHONE_AGENT_ENABLED",
        description="Enable real ai-backend phone calls instead of dev/mock comms.",
    )
    agent_llm_timeout_s: float = Field(
        default=120.0,
        validation_alias="AGENT_LLM_TIMEOUT_S",
        description="Per-agent LLM call timeout in run_agents_async (many roles in parallel).",
    )
    orchestrator_version: str = Field(
        default="v1",
        validation_alias="ORCHESTRATOR_VERSION",
        description="Idempotency/version key for orchestration runs.",
    )
    orchestration_log_file: str | None = Field(
        default="logs/orchestration-trace.log",
        validation_alias="ORCHESTRATION_LOG_FILE",
        description="Optional file path for orchestration trace logging.",
    )
    orchestration_human_log_file: str | None = Field(
        default="logs/orchestration-pretty.log",
        validation_alias="ORCHESTRATION_HUMAN_LOG_FILE",
        description="Human-readable orchestration log (agents, steps). Set empty to disable.",
    )
    intake_default_organization_external_id: str = Field(
        default="org_kghm",
        validation_alias="INTAKE_DEFAULT_ORG_EXTERNAL_ID",
        description="Mongo organizations.external_id used when ingesting a new incident from the UI.",
    )
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias="CORS_ORIGINS",
        description=(
            "Comma-separated browser Origins for CORS (e.g. your Next.js URL). "
            "Include both localhost and 127.0.0.1 if you use either. "
            "The API also allows any port on localhost/127.0.0.1 via regex in dev."
        ),
    )

    @model_validator(mode="after")
    def _resolve_llm_provider(self) -> Self:
        # pydantic-settings: mutate ``self`` and return ``self`` (returning ``model_copy`` is ignored).
        anthropic = (self.anthropic_api_key or "").strip()
        if anthropic:
            self.llm_base_url = self.anthropic_base_url.rstrip("/")
            self.llm_api_key = anthropic
            self.default_chat_model = self.anthropic_chat_model
        elif not (self.llm_api_key or "").strip():
            raise ValueError(
                "Missing LLM credentials: set ANTHROPIC_API_KEY (Claude) or CGC_LLM_API_KEY "
                "(Comtegra GPU Cloud). See agenty/.env.example."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
