from agenty.orchestration.agent_selector import AgentSelector


def test_selector_excludes_orchestrator_from_parallel_council() -> None:
    selector = AgentSelector()

    selected = selector.select(
      hierarchy={
          "role": "Starosta Powiatu",
          "children": [
              {"role": "Wojewoda", "children": []},
              {"role": "Komendant PSP", "children": []},
          ],
      },
      incident={
          "type": "accident",
          "description": "Karambol z wieloma pojazdami.",
          "scope": "powiat",
      },
    )

    assert "orchestrator" not in selected
    assert "starosta" in selected
    assert "wojewoda" in selected
    assert "komendant-psp" in selected
