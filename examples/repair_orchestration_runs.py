"""One-off repair for duplicate or stale orchestration runs."""

from __future__ import annotations

import asyncio
import json

from agenty.agent import AgentRuntime
from agenty.config import get_settings
from agenty.db.mongo import MongoConnector
from agenty.mcp_gateway import CommsMockMCPServer, MCPGateway, ResourceCrudMCPServer, ScenarioGenMCPServer
from agenty.orchestration.engine import OrchestrationEngine
from agenty.orchestration.repair_runs import repair_duplicate_runs
from agenty.orchestration.repository import OrchestrationRepository


def main() -> None:
    settings = get_settings()
    connector = MongoConnector(settings)
    repository = OrchestrationRepository(connector)
    runtime = AgentRuntime(settings=settings)
    mcp = MCPGateway(
        [
            ResourceCrudMCPServer(
                api_base_url=settings.nextjs_api_base_url,
                api_token=settings.nextjs_api_token,
                timeout_s=settings.nextjs_http_timeout_s,
            ),
            ScenarioGenMCPServer(),
            CommsMockMCPServer(runtime.llm),
        ]
    )
    engine = OrchestrationEngine(
        repository=repository,
        runtime=runtime,
        mcp=mcp,
        orchestrator_version=settings.orchestrator_version,
    )

    decisions = repair_duplicate_runs(repository, orchestrator_version=settings.orchestrator_version)
    for decision in decisions:
        if decision.needs_resume:
            asyncio.run(engine.resume(decision.canonical_run_id))

    print(
        json.dumps(
            [
                {
                    "incident_id": item.incident_id,
                    "canonical_run_id": item.canonical_run_id,
                    "superseded_run_ids": list(item.superseded_run_ids),
                    "needs_resume": item.needs_resume,
                    "scenario_version_id": item.scenario_version_id,
                }
                for item in decisions
            ],
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
