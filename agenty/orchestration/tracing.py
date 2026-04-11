"""Structured logging helpers for orchestration visibility."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_LOGGER_NAME = "agenty.orchestration"
_configured = False


def configure_orchestration_logging(log_file: str | None = None) -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if not _configured:
        logger.handlers.clear()
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream)
        logger.propagate = False
        _configured = True
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        has_file = any(isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == str(path) for handler in logger.handlers)
        if not has_file:
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(file_handler)
    return logger


def trace_event(event: str, **data: Any) -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    payload = {"event": event, **data}
    logger.info(json.dumps(payload, default=str, ensure_ascii=False))
