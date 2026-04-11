"""Application settings (CGC LLM — OpenAI-compatible API)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: repository ``agenty/`` folder (parent of the ``agenty`` Python package).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Environment-backed configuration.

    API reference: https://docs.cgc.comtegra.cloud/llm-api
    Base URL defaults to the hosted Comtegra endpoint; override for self-managed instances.
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
    llm_api_key: str = Field(validation_alias="CGC_LLM_API_KEY")
    default_chat_model: str = Field(
        default="llama3-8b",
        validation_alias="CGC_LLM_CHAT_MODEL",
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
