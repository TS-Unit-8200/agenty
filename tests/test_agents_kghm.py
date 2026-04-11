"""Tests for KGHM agent loading and selector."""

from __future__ import annotations

from pathlib import Path

from agenty.agent import AgentRegistry
from agenty.orchestration.agent_selector_kghm import AgentSelectorKGHM

_KGHM_DIR = Path(__file__).resolve().parent.parent / "agents_kghm"

EXPECTED_AGENT_IDS = sorted([
    "orchestrator-kghm",
    "czlonek-zarzadu-operacje",
    "dyrektor-zuz",
    "kierownik-ruchu-zakladu",
    "dyspozytor-ratownictwa",
    "szef-ochrony",
    "lekarz-zakladowy",
    "logistyk-kghm",
    "csirt-ot",
    "rzecznik-kryzysowy",
])


# ---------- Registry loading ----------


def test_kghm_registry_loads_all_agents() -> None:
    registry = AgentRegistry(agents_dir=_KGHM_DIR)
    loaded = registry.list_ids()
    assert loaded == EXPECTED_AGENT_IDS, f"Expected {EXPECTED_AGENT_IDS}, got {loaded}"


def test_kghm_registry_parses_titles() -> None:
    registry = AgentRegistry(agents_dir=_KGHM_DIR)
    for agent_id in EXPECTED_AGENT_IDS:
        defn = registry.get(agent_id)
        assert defn.title, f"Agent {agent_id} has empty title"
        assert defn.instructions, f"Agent {agent_id} has empty instructions"
        assert defn.source_path is not None


def test_kghm_agents_have_required_sections() -> None:
    """Every KGHM agent file should contain key sections matching the contract."""
    registry = AgentRegistry(agents_dir=_KGHM_DIR)
    for agent_id in EXPECTED_AGENT_IDS:
        defn = registry.get(agent_id)
        assert "## Rola" in defn.instructions, (
            f"Agent {agent_id} missing section '## Rola'"
        )
        # Orchestrator uses "Format wyjsciowy" instead of "Format odpowiedzi"
        has_format = (
            "## Format odpowiedzi" in defn.instructions
            or "## Format wyjsciowy" in defn.instructions
        )
        assert has_format, (
            f"Agent {agent_id} missing section '## Format odpowiedzi' or '## Format wyjsciowy'"
        )


# ---------- Selector ----------


def test_kghm_selector_includes_mandatory_and_orchestrator() -> None:
    selector = AgentSelectorKGHM()
    agents = selector.select(
        hierarchy={"role": "kierownik-ruchu", "children": []},
        incident={"type": "zawal", "description": "zawal stropu w rejonie A", "scope": "zaklad"},
    )
    assert "orchestrator-kghm" in agents
    assert "dyspozytor-ratownictwa" in agents
    assert "szef-ochrony" in agents
    assert "lekarz-zakladowy" in agents
    assert "logistyk-kghm" in agents


def test_kghm_selector_adds_csirt_for_cyber() -> None:
    selector = AgentSelectorKGHM()
    agents = selector.select(
        hierarchy={"role": "dyrektor-zakladu", "children": []},
        incident={"type": "cyber", "description": "anomalia SCADA", "scope": "zaklad"},
    )
    assert "csirt-ot" in agents


def test_kghm_selector_adds_rzecznik_for_public_incident() -> None:
    selector = AgentSelectorKGHM()
    agents = selector.select(
        hierarchy={"role": "dyrektor-zuz", "children": []},
        incident={"type": "wypadek", "description": "ofiary smiertelne", "scope": "zaklad"},
    )
    assert "rzecznik-kryzysowy" in agents


def test_kghm_selector_escalates_for_grupa_scope() -> None:
    selector = AgentSelectorKGHM()
    agents = selector.select(
        hierarchy={"role": "czlonek-zarzadu", "children": []},
        incident={"type": "blackout", "description": "brak zasilania", "scope": "grupa"},
    )
    assert "czlonek-zarzadu-operacje" in agents
    assert "dyrektor-zuz" in agents


def test_kghm_selector_adds_kierownik_for_zaklad_scope() -> None:
    selector = AgentSelectorKGHM()
    agents = selector.select(
        hierarchy={"role": "", "children": []},
        incident={"type": "pozar", "description": "pozar w wyrobisku", "scope": "kopalnia"},
    )
    assert "kierownik-ruchu-zakladu" in agents
    assert "dyrektor-zuz" in agents
