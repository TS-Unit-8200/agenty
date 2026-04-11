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
    """Linear council pipeline: hierarchy → agents → reconcile → scenarios → resources → comms."""
    g = StateGraph(CrisisGraphState)
    g.add_node("fetch_hierarchy", nodes.fetch_hierarchy)
    g.add_node("select_agents", nodes.select_agents)
    g.add_node("run_agents_async", nodes.run_agents_async)
    g.add_node("resolve_conflicts", nodes.resolve_conflicts)
    g.add_node("generate_scenarios", nodes.generate_scenarios)
    g.add_node("sync_resources", nodes.sync_resources)
    g.add_node("comms_mock_call", nodes.comms_mock_call)

    g.add_edge(START, "fetch_hierarchy")
    g.add_edge("fetch_hierarchy", "select_agents")
    g.add_edge("select_agents", "run_agents_async")
    g.add_edge("run_agents_async", "resolve_conflicts")
    g.add_edge("resolve_conflicts", "generate_scenarios")
    g.add_edge("generate_scenarios", "sync_resources")
    g.add_edge("sync_resources", "comms_mock_call")
    g.add_edge("comms_mock_call", END)

    return g.compile(checkpointer=checkpointer)
