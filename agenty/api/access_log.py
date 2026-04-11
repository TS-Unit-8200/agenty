"""High-visibility HTTP access logging (stderr, always flushed)."""

from __future__ import annotations

import logging
import sys
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

ACCESS = logging.getLogger("agenty.access")


def agenty_echo(msg: str) -> None:
    """Print one line to stderr with flush — shows under uvicorn even when logging is misconfigured."""
    print(msg, file=sys.stderr, flush=True)


def configure_access_logging() -> None:
    if ACCESS.handlers:
        return
    ACCESS.setLevel(logging.INFO)
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[agenty] %(message)s"))
    ACCESS.addHandler(h)
    ACCESS.propagate = False


def _client_host(request: Request) -> str:
    if request.client:
        return request.client.host
    return "?"


def _query_preview(request: Request, *, max_len: int = 120) -> str:
    q = request.url.query
    if not q:
        return "-"
    return q if len(q) <= max_len else q[: max_len - 3] + "..."


class AgentyAccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)
        if path in ("/favicon.ico",) or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)
        t0 = time.perf_counter()
        clen = request.headers.get("content-length", "-")
        line = (
            f"[agenty] HIT → {request.method} {path} "
            f"client={_client_host(request)} "
            f"content-length={clen} "
            f"query={_query_preview(request)}"
        )
        agenty_echo(line)
        ACCESS.info("→ %s %s client=%s", request.method, path, _client_host(request))
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        agenty_echo(f"[agenty] DONE ← {request.method} {path} → HTTP {response.status_code} ({ms:.0f} ms)")
        ACCESS.info("← %s %s %s (%.0f ms)", request.method, path, response.status_code, ms)
        return response
