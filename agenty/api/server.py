"""Application factory for orchestration API."""

from __future__ import annotations

from fastapi import FastAPI

from agenty.agent import AgentRuntime
from agenty.api.routes_orchestration import create_orchestration_router
from agenty.config import get_settings
from agenty.db.mongo import MongoConnector
from agenty.mcp_gateway import CommsMockMCPServer, MCPGateway, ResourceCrudMCPServer, ScenarioGenMCPServer
from agenty.orchestration.engine import OrchestrationEngine
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.tracing import configure_orchestration_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_orchestration_logging(settings.orchestration_log_file)
    connector = MongoConnector(settings)
    repository = OrchestrationRepository(connector)
    runtime = AgentRuntime(settings=settings)

    resource_server = ResourceCrudMCPServer(
        api_base_url=settings.nextjs_api_base_url,
        api_token=settings.nextjs_api_token,
    )
    scenario_server = ScenarioGenMCPServer()
    comms_server = CommsMockMCPServer(runtime.llm)
    mcp = MCPGateway([resource_server, scenario_server, comms_server])

    engine = OrchestrationEngine(
        repository=repository,
        runtime=runtime,
        mcp=mcp,
        orchestrator_version=settings.orchestrator_version,
    )

    app = FastAPI(title="Agenty Orchestration API", version="0.1.0")
    app.include_router(create_orchestration_router(engine=engine, repository=repository))
    return app


def app_factory() -> FastAPI:
    return create_app()
