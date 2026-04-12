"""Compiled LangGraph workflow for crisis orchestration."""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agenty.orchestration.crisis_graph_state import CrisisGraphState
from agenty.orchestration.crisis_workflow_nodes import CrisisWorkflowNodes


def build_crisis_graph(
    nodes: CrisisWorkflowNodes,
    *,
    checkpointer: BaseCheckpointSaver,
):
    """Linear council pipeline: hierarchy -> council -> reconciliation -> orchestrator."""
    g = StateGraph(CrisisGraphState)
    g.add_node("fetch_hierarchy", nodes.fetch_hierarchy)
    g.add_node("select_agents", nodes.select_agents)
    g.add_node("run_agents_async", nodes.run_agents_async)
    g.add_node("resolve_conflicts", nodes.resolve_conflicts)
    g.add_node("plan_external_info", nodes.plan_external_info)
    g.add_node("await_external_info", nodes.await_external_info)
    g.add_node("refresh_agent_after_call", nodes.refresh_agent_after_call)
    g.add_node("run_orchestrator", nodes.run_orchestrator)
    g.add_node("generate_scenarios", nodes.generate_scenarios)
    g.add_node("sync_resources", nodes.sync_resources)

    g.add_edge(START, "fetch_hierarchy")
    g.add_edge("fetch_hierarchy", "select_agents")
    g.add_edge("select_agents", "run_agents_async")
    g.add_edge("run_agents_async", "resolve_conflicts")
    g.add_edge("resolve_conflicts", "plan_external_info")
    g.add_edge("plan_external_info", "await_external_info")
    g.add_edge("await_external_info", "refresh_agent_after_call")
    g.add_edge("refresh_agent_after_call", "run_orchestrator")
    g.add_edge("run_orchestrator", "generate_scenarios")
    g.add_edge("generate_scenarios", "sync_resources")
    g.add_edge("sync_resources", END)

    return g.compile(checkpointer=checkpointer)
