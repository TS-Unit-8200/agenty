"""Scenario synthesis from reconciled orchestration context."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from agenty.db.models import Scenario, ScenarioActions
from agenty.mcp_gateway.base import MCPGateway
from agenty.orchestration.models import ScenarioVersion
from agenty.orchestration.tracing import trace_event


class ScenarioService:
    def __init__(self, mcp: MCPGateway) -> None:
        self._mcp = mcp

    def build(
        self,
        *,
        run_id: str,
        incident_id: str,
        priority: str,
        affected_population: int,
        resource_count: int,
        reconciliation: dict[str, object],
    ) -> ScenarioVersion:
        costs = json.loads(
            self._mcp.call_tool(
                "scenario_estimate_cost",
                {
                    "affected_population": affected_population,
                    "resource_count": resource_count,
                },
            )
        )
        estimated_cost = str(costs.get("estimated_cost", "100000"))

        scenarios = [
            Scenario(
                id=str(uuid4()),
                label="A",
                title="Szybka stabilizacja",
                type="fast",
                estimated_cost=estimated_cost,
                time_to_resolve="2-6h",
                is_recommended=False,
                risks=["Wysokie zuzycie zasobow", "Mozliwe przeciazenie operacyjne"],
                benefits=["Najszybsze opanowanie sytuacji"],
                consequences_of_inaction="Ryzyko dalszej eskalacji.",
                actions=ScenarioActions(
                    h2=["Zabezpieczyc ludzi i IK", "Uruchomic tryb kryzysowy"],
                    h12=["Ustabilizowac obszary krytyczne", "Rotowac zasoby"],
                    h24=["Plan odbudowy i raport"],
                ),
            ),
            Scenario(
                id=str(uuid4()),
                label="B",
                title="Optymalizacja zasobow",
                type="balanced",
                estimated_cost=estimated_cost,
                time_to_resolve="6-12h",
                is_recommended=False,
                risks=["Wolniejsza stabilizacja"],
                benefits=["Lepsza kontrola kosztow", "Mniejsze obciazenie sluzb"],
                consequences_of_inaction="Rosnace opoznienia i presja operacyjna.",
                actions=ScenarioActions(
                    h2=["Priorytetyzowac zadania i zasoby", "Uruchomic monitoring co 30 min"],
                    h12=["Realokowac zasoby miedzy obszarami"],
                    h24=["Ocena efektywnosci i korekta planu"],
                ),
            ),
            Scenario(
                id=str(uuid4()),
                label="C",
                title="Obrona infrastruktury krytycznej",
                type="critical_infra",
                estimated_cost=estimated_cost,
                time_to_resolve="8-18h",
                is_recommended=False,
                risks=["Wydluzony czas pelnego przywrocenia"],
                benefits=["Minimalizacja ryzyka systemowego"],
                consequences_of_inaction="Niechroniona IK moze doprowadzic do kaskady awarii.",
                actions=ScenarioActions(
                    h2=["Skupic zasoby na IK", "Wzmocnic ochrone obiektow"],
                    h12=["Zapewnic ciaglosc krytycznych uslug"],
                    h24=["Plan etapowego przywracania"],
                ),
            ),
        ]

        scored: list[tuple[float, Scenario]] = []
        for scenario in scenarios:
            risk_score = json.loads(
                self._mcp.call_tool(
                    "scenario_risk_score",
                    {
                        "risks": scenario.risks,
                        "priority": priority,
                    },
                )
            )
            score = float(risk_score.get("score", 0.0))
            scored.append((score, scenario))

        scored.sort(key=lambda item: item[0])
        best_score, best_scenario = scored[0]
        for _score, scenario in scored:
            scenario.is_recommended = scenario is best_scenario
        trace_event("orchestration.scenario.scored", run_id=run_id, best_label=best_scenario.label, best_score=best_score)

        recommendation = best_scenario
        raw_gaps = reconciliation.get("gaps", [])
        gaps = raw_gaps if isinstance(raw_gaps, list) else []
        confidence = max(0.35, 0.9 - 0.12 * len(gaps))
        return ScenarioVersion(
            id=str(uuid4()),
            run_id=run_id,
            incident_id=incident_id,
            created_at=datetime.now(UTC),
            recommendation_label=recommendation.label,
            confidence=round(confidence, 2),
            scenarios=scenarios,
            rationale="Rekomendacja oparta o ryzyko, krytycznosc i dostepnosc zasobow.",
        )
