"""MongoDB client wired from ``DATABASE_URL`` (and optional ``MONGODB_DATABASE``)."""

from __future__ import annotations

from typing import Any

import certifi
from pymongo import MongoClient
from pymongo.database import Database

from agenty.config import Settings, get_settings


def _mongo_client_kwargs(database_url: str) -> dict[str, Any]:
    """Extra PyMongo options; use Mozilla CA bundle for TLS URLs (Atlas, TLS ``mongodb://``)."""
    kwargs: dict[str, Any] = {
        "appname": "agenty",
        "serverSelectionTimeoutMS": 15_000,
    }
    lower = database_url.strip().lower()
    if lower.startswith("mongodb+srv://") or "tls=true" in lower or "ssl=true" in lower:
        # Avoid relying on the OS trust store alone (helps some macOS / dev setups and Atlas).
        kwargs["tlsCAFile"] = certifi.where()
    return kwargs


class MongoConnector:
    """Thin wrapper around PyMongo using project :class:`~agenty.config.Settings`."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        url = (self._settings.database_url or "").strip()
        if not url:
            raise ValueError(
                "DATABASE_URL is missing or empty. Set it in .env (see .env.example)."
            )
        self._client: MongoClient[dict[str, Any]] = MongoClient(url, **_mongo_client_kwargs(url))

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
