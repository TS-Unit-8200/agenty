"""Execution wrappers for council agents and the orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from agenty.agent import AgentRuntime, AgentToolEvent
from agenty.context import AgentContext
from agenty.orchestration.agent_phone_tool import CouncilPhoneToolProvider
from agenty.orchestration.exceptions import AgentSessionPause
from agenty.orchestration.models import AgentRun, AgentToolSession, ExternalInfoRequest
from agenty.orchestration.repository import OrchestrationRepository
from agenty.orchestration.tracing import trace_event, trace_human_block


@dataclass
class AgentExecutionOutcome:
    agent_run: AgentRun
    paused: bool = False
    session: AgentToolSession | None = None
    external_request: ExternalInfoRequest | None = None


def _attach_tool_event(agent_run: AgentRun, tool_event: AgentToolEvent | None) -> AgentRun:
    if tool_event is None:
        agent_run.tool_status = "idle"
        return agent_run
    agent_run.tool_status = tool_event.status
    agent_run.tool_notice = tool_event.notice
    agent_run.tool_resource_id = tool_event.resource_id
    agent_run.tool_resource_name = tool_event.resource_name
    return agent_run


class AgentRunner:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        timeout_s: float = 45.0,
        max_concurrency: int = 6,
        phone_poll_interval_s: float = 10.0,
    ) -> None:
        self._runtime = runtime
        self._timeout_s = timeout_s
        self._max_concurrency = max_concurrency
        self._phone_poll_interval_s = phone_poll_interval_s

    @property
    def default_timeout_s(self) -> float:
        return self._timeout_s

    async def run(
        self,
        agent_ids: list[str],
        prompt: str,
        context_sections: dict[str, str],
        *,
        timeout_s: float | None = None,
        execution_mode: str | None = None,
    ) -> list[AgentRun]:
        trace_event("orchestration.agent.batch.start", agent_ids=agent_ids)
        trace_human_block(
            "Agent batch  |  {count} roles".format(count=len(agent_ids)),
            "Agents: "
            + ", ".join(agent_ids)
            + "\n\nPrompt (preview):\n"
            + (prompt[:2000] + ("..." if len(prompt) > 2000 else "")),
        )
        semaphore = asyncio.Semaphore(self._max_concurrency)
        effective_timeout_s = timeout_s or self._timeout_s

        async def execute(agent_id: str) -> AgentRun:
            async with semaphore:
                started = datetime.now(UTC)
                trace_event("orchestration.agent.start", agent_id=agent_id)
                start_ts = started.timestamp()
                try:
                    context = AgentContext(sections=context_sections)
                    response = await asyncio.wait_for(
                        asyncio.to_thread(self._invoke_agent, agent_id, prompt, context, None, execution_mode),
                        timeout=effective_timeout_s,
                    )
                    finished = datetime.now(UTC)
                    latency_ms = int((finished.timestamp() - start_ts) * 1000)
                    trace_human_block(
                        "Agent '{agent_id}'  |  completed  |  {latency_ms} ms".format(
                            agent_id=agent_id,
                            latency_ms=latency_ms,
                        ),
                        response or "(empty reply)",
                    )
                    return AgentRun(
                        run_id="",
                        agent_id=agent_id,
                        status="completed",
                        started_at=started,
                        finished_at=finished,
                        latency_ms=latency_ms,
                        response=response,
                    )
                except TimeoutError:
                    finished = datetime.now(UTC)
                    latency_ms = int((finished.timestamp() - start_ts) * 1000)
                    err = f"Timed out after {effective_timeout_s}s"
                    trace_human_block(
                        "Agent '{agent_id}'  |  timed_out  |  {latency_ms} ms".format(
                            agent_id=agent_id,
                            latency_ms=latency_ms,
                        ),
                        err,
                    )
                    return AgentRun(
                        run_id="",
                        agent_id=agent_id,
                        status="timed_out",
                        started_at=started,
                        finished_at=finished,
                        latency_ms=latency_ms,
                        error=err,
                    )
                except Exception as exc:  # noqa: BLE001
                    finished = datetime.now(UTC)
                    latency_ms = int((finished.timestamp() - start_ts) * 1000)
                    err = str(exc)
                    trace_human_block(
                        "Agent '{agent_id}'  |  failed  |  {latency_ms} ms".format(
                            agent_id=agent_id,
                            latency_ms=latency_ms,
                        ),
                        err,
                    )
                    return AgentRun(
                        run_id="",
                        agent_id=agent_id,
                        status="failed",
                        started_at=started,
                        finished_at=finished,
                        latency_ms=latency_ms,
                        error=err,
                    )

        results = await asyncio.gather(*(execute(agent_id) for agent_id in agent_ids))
        trace_event("orchestration.agent.batch.complete", total=len(results))
        return results

    async def run_council_agent(
        self,
        *,
        run_id: str,
        incident_id: str,
        agent_id: str,
        prompt: str,
        context_sections: dict[str, str],
        repository: OrchestrationRepository,
        mcp,
        timeout_s: float | None = None,
        execution_mode: str | None = None,
    ) -> AgentExecutionOutcome:
        started = datetime.now(UTC)
        trace_event("orchestration.council.agent.start", run_id=run_id, agent_id=agent_id)
        start_ts = started.timestamp()
        effective_timeout_s = timeout_s or self._timeout_s
        tool_provider = CouncilPhoneToolProvider(
            repository=repository,
            mcp=mcp,
            run_id=run_id,
            incident_id=incident_id,
            agent_id=agent_id,
            phone_poll_interval_s=self._phone_poll_interval_s,
            execution_mode=execution_mode,
        )
        mcp_provider = tool_provider if tool_provider.list_tool_specs() else None
        try:
            context = AgentContext(sections=context_sections)
            response, tool_event = await asyncio.wait_for(
                asyncio.to_thread(
                    self._invoke_agent_with_tool_state,
                    agent_id,
                    prompt,
                    context,
                    mcp_provider,
                    execution_mode,
                ),
                timeout=effective_timeout_s,
            )
            finished = datetime.now(UTC)
            latency_ms = int((finished.timestamp() - start_ts) * 1000)
            trace_human_block(
                "Agent '{agent_id}'  |  completed  |  {latency_ms} ms".format(
                    agent_id=agent_id,
                    latency_ms=latency_ms,
                ),
                response or "(empty reply)",
            )
            return AgentExecutionOutcome(
                agent_run=_attach_tool_event(
                    AgentRun(
                        run_id=run_id,
                        agent_id=agent_id,
                        status="completed",
                        started_at=started,
                        finished_at=finished,
                        latency_ms=latency_ms,
                        response=response,
                    ),
                    tool_event,
                )
            )
        except AgentSessionPause as pause:
            finished = datetime.now(UTC)
            latency_ms = int((finished.timestamp() - start_ts) * 1000)
            trace_human_block(
                "Agent '{agent_id}'  |  waiting_tool  |  {latency_ms} ms".format(
                    agent_id=agent_id,
                    latency_ms=latency_ms,
                ),
                getattr(pause.request, "notice", None) or "Agent czeka na wynik telefonu.",
            )
            return AgentExecutionOutcome(
                agent_run=AgentRun(
                    run_id=run_id,
                    agent_id=agent_id,
                    status="waiting_tool",
                    started_at=started,
                    finished_at=finished,
                    latency_ms=latency_ms,
                    tool_status="waiting_tool",
                    tool_notice=getattr(pause.request, "notice", None),
                    tool_resource_id=getattr(pause.request, "resource_id", None),
                    tool_resource_name=getattr(pause.request, "resource_name", None),
                ),
                paused=True,
                session=AgentToolSession(
                    run_id=run_id,
                    agent_id=agent_id,
                    tool_name="phone_query_resource",
                    tool_call_id=pause.tool_call_id,
                    execution_mode=execution_mode or "default",
                    messages=pause.messages,
                    created_at=started,
                    updated_at=finished,
                ),
                external_request=pause.request,
            )
        except TimeoutError:
            finished = datetime.now(UTC)
            latency_ms = int((finished.timestamp() - start_ts) * 1000)
            err = f"Timed out after {effective_timeout_s}s"
            return AgentExecutionOutcome(
                agent_run=AgentRun(
                    run_id=run_id,
                    agent_id=agent_id,
                    status="timed_out",
                    started_at=started,
                    finished_at=finished,
                    latency_ms=latency_ms,
                    error=err,
                )
            )
        except Exception as exc:  # noqa: BLE001
            finished = datetime.now(UTC)
            latency_ms = int((finished.timestamp() - start_ts) * 1000)
            err = str(exc)
            return AgentExecutionOutcome(
                agent_run=AgentRun(
                    run_id=run_id,
                    agent_id=agent_id,
                    status="failed",
                    started_at=started,
                    finished_at=finished,
                    latency_ms=latency_ms,
                    error=err,
                )
            )

    async def resume_council_agent(
        self,
        *,
        agent_id: str,
        session: AgentToolSession,
        tool_payload: dict[str, object],
        timeout_s: float | None = None,
        execution_mode: str | None = None,
    ) -> AgentRun:
        started = datetime.now(UTC)
        start_ts = started.timestamp()
        effective_timeout_s = timeout_s or self._timeout_s
        try:
            response, tool_event = await asyncio.wait_for(
                asyncio.to_thread(
                    self._resume_agent_after_tool_with_state,
                    agent_id,
                    session.messages,
                    session.tool_call_id,
                    session.tool_name,
                    tool_payload,
                    execution_mode or session.execution_mode,
                ),
                timeout=effective_timeout_s,
            )
            finished = datetime.now(UTC)
            latency_ms = int((finished.timestamp() - start_ts) * 1000)
            return _attach_tool_event(
                AgentRun(
                    run_id=session.run_id,
                    agent_id=agent_id,
                    status="completed",
                    started_at=started,
                    finished_at=finished,
                    latency_ms=latency_ms,
                    response=response,
                ),
                tool_event,
            )
        except TimeoutError:
            finished = datetime.now(UTC)
            latency_ms = int((finished.timestamp() - start_ts) * 1000)
            return _attach_tool_event(
                AgentRun(
                    run_id=session.run_id,
                    agent_id=agent_id,
                    status="timed_out",
                    started_at=started,
                    finished_at=finished,
                    latency_ms=latency_ms,
                    error=f"Timed out after {effective_timeout_s}s",
                ),
                None,
            )
        except Exception as exc:  # noqa: BLE001
            finished = datetime.now(UTC)
            latency_ms = int((finished.timestamp() - start_ts) * 1000)
            return _attach_tool_event(
                AgentRun(
                    run_id=session.run_id,
                    agent_id=agent_id,
                    status="failed",
                    started_at=started,
                    finished_at=finished,
                    latency_ms=latency_ms,
                    error=str(exc),
                ),
                None,
            )

    def _invoke_agent(
        self,
        agent_id: str,
        prompt: str,
        context: AgentContext,
        mcp_provider,
        execution_mode: str | None,
    ) -> str:
        session = self._runtime.start(agent_id, context=context, execution_mode=execution_mode, mcp=mcp_provider)
        return session.say(prompt)

    def _invoke_agent_with_tool_state(
        self,
        agent_id: str,
        prompt: str,
        context: AgentContext,
        mcp_provider,
        execution_mode: str | None,
    ) -> tuple[str, AgentToolEvent | None]:
        session = self._runtime.start(
            agent_id,
            context=context,
            execution_mode=execution_mode,
            mcp=mcp_provider,
        )
        response = session.say(prompt)
        return response, session.last_tool_event

    def _resume_agent_after_tool_with_state(
        self,
        agent_id: str,
        messages: list[dict[str, object]],
        tool_call_id: str,
        tool_name: str,
        tool_payload: dict[str, object],
        execution_mode: str | None,
    ) -> tuple[str, AgentToolEvent | None]:
        session = self._runtime.start(agent_id, messages=messages, execution_mode=execution_mode)
        response = session.resume_after_tool(tool_call_id, tool_payload, tool_name=tool_name)
        return response, session.last_tool_event
