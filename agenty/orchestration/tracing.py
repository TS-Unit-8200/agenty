"""Structured logging helpers for orchestration visibility."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOGGER_NAME = "agenty.orchestration"
_HUMAN_LOGGER_NAME = "agenty.orchestration.human"
_configured = False
_human_configured_path: str | None = None


def configure_orchestration_logging(
    log_file: str | None = None,
    *,
    human_log_file: str | None = None,
) -> logging.Logger:
    global _configured, _human_configured_path
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

    h_raw = (human_log_file or "").strip()
    if h_raw and h_raw.lower() not in ("none", "false", "0"):
        hpath = Path(h_raw)
        if _human_configured_path != str(hpath.resolve()):
            human = logging.getLogger(_HUMAN_LOGGER_NAME)
            human.handlers.clear()
            human.setLevel(logging.INFO)
            human.propagate = False
            hpath.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(hpath, encoding="utf-8", mode="a")
            fh.setFormatter(logging.Formatter("%(message)s"))
            human.addHandler(fh)
            _human_configured_path = str(hpath.resolve())

    return logger


def trace_human_block(title: str, body: str, *, width: int = 76) -> None:
    """Append a framed, timestamped block to the human log (and stderr)."""
    human = logging.getLogger(_HUMAN_LOGGER_NAME)
    if not human.handlers:
        return
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    rule = "━" * min(width, 96)
    trimmed = (body or "").rstrip()
    if len(trimmed) > 48_000:
        trimmed = trimmed[:48_000] + "\n… [truncated]"
    msg = f"\n{rule}\n  {ts}  │  {title}\n{rule}\n{trimmed}\n{rule}\n"
    human.info(msg)


def trace_event(event: str, **data: Any) -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    payload = {"event": event, **data}
    logger.info(json.dumps(payload, default=str, ensure_ascii=False))
