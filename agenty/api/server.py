"""Application factory for orchestration API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agenty.agent import AgentRuntime
from agenty.api.access_log import AgentyAccessLogMiddleware, agenty_echo, configure_access_logging
from agenty.api.routes_incident_report import create_incident_report_router
from agenty.api.routes_orchestration import create_orchestration_router
from agenty.config import get_settings
from agenty.db.mongo import MongoConnector
from agenty.mcp_gateway import (
    CommsMockMCPServer,
    MCPGateway,
    PhoneCallMCPServer,
    ResourceCrudMCPServer,
    ScenarioGenMCPServer,
)
from agenty.orchestration.engine import OrchestrationEngine
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.tracing import configure_orchestration_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_access_logging()
    _human = (settings.orchestration_human_log_file or "").strip()
    configure_orchestration_logging(
        settings.orchestration_log_file,
        human_log_file=_human or None,
    )
    connector = MongoConnector(settings)
    repository = OrchestrationRepository(connector)
    runtime = AgentRuntime(settings=settings)

    resource_server = ResourceCrudMCPServer(
        api_base_url=settings.nextjs_api_base_url,
        api_token=settings.nextjs_api_token,
        timeout_s=settings.nextjs_http_timeout_s,
    )
    scenario_server = ScenarioGenMCPServer()
    phone_server = (
        PhoneCallMCPServer(
            base_url=settings.phone_agent_base_url,
            api_token=settings.phone_agent_api_token or "",
        )
        if settings.phone_agent_enabled and (settings.phone_agent_api_token or "").strip()
        else CommsMockMCPServer(runtime.llm)
    )
    mcp = MCPGateway([resource_server, scenario_server, phone_server])

    engine = OrchestrationEngine(
        repository=repository,
        runtime=runtime,
        mcp=mcp,
        orchestrator_version=settings.orchestrator_version,
    )

    app = FastAPI(title="Agenty Orchestration API", version="0.1.0")
    app.add_middleware(AgentyAccessLogMiddleware)
    cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if not cors_origins:
        cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # Browsers treat localhost vs 127.0.0.1 as different origins; regex covers any port for local dev.
    _local_dev_origin = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=_local_dev_origin,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    agenty_echo(
        f"[agenty] CORS allow_origins={cors_origins!r} + regex localhost/127.0.0.1:anyPort for dev",
    )
    app.include_router(create_orchestration_router(engine=engine, repository=repository))
    app.include_router(
        create_incident_report_router(
            engine=engine,
            repository=repository,
            runtime=runtime,
            settings=settings,
        )
    )
    @app.on_event("startup")
    async def _restore_waiting_phone_runs() -> None:
        restored = engine.restore_waiting_runs()
        agenty_echo(f"[agenty] startup restore_waiting_runs -> restored_watchers={restored}")

    agenty_echo("[agenty] create_app: ready (Mongo + routers mounted)")
    return app


def app_factory() -> FastAPI:
    return create_app()
