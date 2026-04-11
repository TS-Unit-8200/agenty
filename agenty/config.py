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


@lru_cache
def get_settings() -> Settings:
    return Settings()
