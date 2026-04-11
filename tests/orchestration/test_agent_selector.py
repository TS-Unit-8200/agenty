from agenty.orchestration.agent_selector import AgentSelector


def test_selector_includes_mandatory_and_orchestrator() -> None:
    selector = AgentSelector()
    agents = selector.select(
        hierarchy={"role": "Wojt", "children": []},
        incident={"type": "infrastructure", "description": "awaria", "scope": "gminny"},
    )
    assert "orchestrator" in agents
    assert "komendant-psp" in agents
    assert "logistyk" in agents


def test_selector_adds_abw_for_terror_signal() -> None:
    selector = AgentSelector()
    agents = selector.select(
        hierarchy={"role": "Starosta", "children": []},
        incident={"type": "terror", "description": "possible sabotage", "scope": "powiatowy"},
    )
    assert "dyrektor-abw" in agents
    assert "starosta" in agents
