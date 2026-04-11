"""Async execution wrapper for role agents."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from agenty.agent import AgentRuntime
from agenty.context import AgentContext
from agenty.orchestration.models import AgentRun
from agenty.orchestration.tracing import trace_event


class AgentRunner:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        timeout_s: float = 45.0,
        max_concurrency: int = 6,
    ) -> None:
        self._runtime = runtime
        self._timeout_s = timeout_s
        self._max_concurrency = max_concurrency

    async def run(self, agent_ids: list[str], prompt: str, context_sections: dict[str, str]) -> list[AgentRun]:
        trace_event("orchestration.agent.batch.start", agent_ids=agent_ids)
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def execute(agent_id: str) -> AgentRun:
            async with semaphore:
                started = datetime.now(UTC)
                trace_event("orchestration.agent.start", agent_id=agent_id)
                start_ts = started.timestamp()
                try:
                    context = AgentContext(sections=context_sections)
                    response = await asyncio.wait_for(
                        asyncio.to_thread(self._invoke_agent, agent_id, prompt, context),
                        timeout=self._timeout_s,
                    )
                    finished = datetime.now(UTC)
                    return AgentRun(
                        run_id="",
                        agent_id=agent_id,
                        status="completed",
                        started_at=started,
                        finished_at=finished,
                        latency_ms=int((finished.timestamp() - start_ts) * 1000),
                        response=response,
                    )
                except TimeoutError:
                    finished = datetime.now(UTC)
                    return AgentRun(
                        run_id="",
                        agent_id=agent_id,
                        status="timed_out",
                        started_at=started,
                        finished_at=finished,
                        latency_ms=int((finished.timestamp() - start_ts) * 1000),
                        error=f"Timed out after {self._timeout_s}s",
                    )
                except Exception as exc:  # noqa: BLE001
                    finished = datetime.now(UTC)
                    return AgentRun(
                        run_id="",
                        agent_id=agent_id,
                        status="failed",
                        started_at=started,
                        finished_at=finished,
                        latency_ms=int((finished.timestamp() - start_ts) * 1000),
                        error=str(exc),
                    )

        results = await asyncio.gather(*(execute(agent_id) for agent_id in agent_ids))
        trace_event("orchestration.agent.batch.complete", total=len(results))
        return results

    def _invoke_agent(self, agent_id: str, prompt: str, context: AgentContext) -> str:
        session = self._runtime.start(agent_id, context=context)
        return session.say(prompt)
