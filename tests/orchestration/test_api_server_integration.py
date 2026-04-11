"""Exercise the real FastAPI app from ``agenty.api.server.create_app``.

These tests are skipped unless ``DATABASE_URL`` and LLM credentials (``ANTHROPIC_API_KEY`` or
``CGC_LLM_API_KEY``) resolve
via ``agenty/.env`` (or the process environment): the same bar as running the
API locally.

They intentionally use a random ``incident_id`` that is not in Mongo so the
background workflow fails at ``fetch_hierarchy`` after ``POST`` returns. That
still proves the production router, engine wiring, and persistence for the
created run. To drive a full pipeline to ``/result``, seed an organization with
that incident and set ``TEST_ORCHESTRATION_INCIDENT_ID`` (future extension).

**Requires ``DATABASE_URL``** (Mongo) **and** ``ANTHROPIC_API_KEY`` or ``CGC_LLM_API_KEY``: the same as
``create_app()`` / ``MongoConnector``. LLM-only ``.env`` is not enough.

If Atlas fails with ``SSL handshake failed`` / ``TLSV1_ALERT_INTERNAL_ERROR``, the
client now uses **certifi** for the CA bundle. If it still fails, try Python
**3.12 or 3.13** (3.14 + OpenSSL can disagree with some Atlas endpoints), VPN
off, and Atlas **Network Access** for your IP.

Run only integration tests::

    pytest -m integration -v

Show HTTP JSON in the terminal (recommended when debugging)::

    pytest -m integration tests/orchestration/test_api_server_integration.py -v -s

Optional: use a real incident from Mongo (full pipeline / ``/result``)::

    export TEST_ORCHESTRATION_INCIDENT_ID='<id from your orgs/incidents>'
    export TEST_ORCHESTRATION_ORG_ID='org-slug-or-id'   # optional, default pytest-org

Fast default (exclude integration)::

    pytest -m \"not integration\"
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _log_response(title: str, response: Any) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}", flush=True)
    print(f"HTTP {response.status_code}", flush=True)
    try:
        body = response.json()
        print(json.dumps(body, indent=2, default=str, ensure_ascii=False), flush=True)
    except Exception:
        print(response.text[:8000] if response.text else "(empty body)", flush=True)


def _integration_skip_reason() -> str | None:
    try:
        from agenty.config import get_settings

        s = get_settings()
    except Exception as exc:
        return f"Settings could not load ({type(exc).__name__}: {exc}). Check agenty/.env."
    if not (s.database_url or "").strip():
        return (
            "DATABASE_URL is missing or empty. Integration uses create_app() which "
            "connects to Mongo; add DATABASE_URL (and MONGODB_DATABASE if needed) to agenty/.env."
        )
    if not (s.llm_api_key or "").strip():
        return "ANTHROPIC_API_KEY or CGC_LLM_API_KEY is missing or empty in agenty/.env."
    return None


@pytest.mark.integration
def test_create_app_post_orchestration_and_get_run() -> None:
    skip = _integration_skip_reason()
    if skip:
        pytest.skip(skip)

    from agenty.api.server import create_app

    env_incident = (os.environ.get("TEST_ORCHESTRATION_INCIDENT_ID") or "").strip()
    env_org = (os.environ.get("TEST_ORCHESTRATION_ORG_ID") or "pytest-org").strip()
    incident_id = env_incident or f"pytest-missing-incident-{uuid.uuid4().hex}"
    org_id = env_org

    print(
        f"\n[integration] incident_id={incident_id!r} org_id={org_id!r} "
        f"(set TEST_ORCHESTRATION_INCIDENT_ID for a real DB incident)\n",
        flush=True,
    )

    with TestClient(create_app()) as client:
        r = client.post(
            "/orchestrations",
            json={"incident_id": incident_id, "org_id": org_id},
        )
        _log_response("POST /orchestrations", r)
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload.get("status") == "created"
        run_id = payload["run_id"]
        assert run_id

        st = client.get(f"/orchestrations/{run_id}")
        _log_response(f"GET /orchestrations/{run_id}", st)
        assert st.status_code == 200, st.text
        data = st.json()
        assert data["run"]["id"] == run_id
        assert isinstance(data["steps"], list)

        res = client.get(f"/orchestrations/{run_id}/result")
        _log_response(f"GET /orchestrations/{run_id}/result", res)
        assert res.status_code == 200, res.text
