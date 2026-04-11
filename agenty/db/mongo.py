"""MongoDB client wired from ``DATABASE_URL`` (and optional ``MONGODB_DATABASE``)."""

from __future__ import annotations

from typing import Any

from pymongo import MongoClient
from pymongo.database import Database

from agenty.config import Settings, get_settings


class MongoConnector:
    """Thin wrapper around PyMongo using project :class:`~agenty.config.Settings`."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        url = (self._settings.database_url or "").strip()
        if not url:
            raise ValueError(
                "DATABASE_URL is missing or empty. Set it in .env (see .env.example)."
            )
        self._client: MongoClient[dict[str, Any]] = MongoClient(
            url,
            appname="agenty",
            serverSelectionTimeoutMS=15_000,
        )

    @property
    def client(self) -> MongoClient[dict[str, Any]]:
        return self._client

    def get_database(self, name: str | None = None) -> Database[dict[str, Any]]:
        """Return a database, preferring ``name``, then ``MONGODB_DATABASE``, then URI path."""
        if name is not None:
            return self._client[name]
        configured = (self._settings.mongodb_database or "").strip()
        if configured:
            return self._client[configured]
        default = self._client.get_default_database()
        if default is not None:
            return default
        raise ValueError(
            "Mongo URI has no database path. Set MONGODB_DATABASE in .env "
            "(e.g. the Atlas database name you created)."
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MongoConnector:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
