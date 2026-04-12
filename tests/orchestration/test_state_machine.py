from agenty.orchestration.state_machine import is_valid_transition, next_state, previous_state


def test_next_state_advances_on_happy_path() -> None:
    assert next_state("created") == "fetch_hierarchy"
    assert next_state("fetch_hierarchy") == "select_agents"
    assert next_state("resolve_conflicts") == "run_orchestrator"


def test_valid_transition_rejects_jump_over_steps() -> None:
    assert is_valid_transition("created", "fetch_hierarchy")
    assert not is_valid_transition("created", "run_agents_async")


def test_terminal_state_cannot_transition() -> None:
    assert not is_valid_transition("completed", "fetch_hierarchy")


def test_previous_state_returns_prior_step() -> None:
    assert previous_state("select_agents") == "fetch_hierarchy"
