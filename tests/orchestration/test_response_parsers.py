from agenty.orchestration.response_parsers import (
    build_scenario_version_from_orchestrator_report,
    summarize_agent_response,
)


def test_summarize_agent_response_prefers_structured_sections() -> None:
    summary = summarize_agent_response(
        agent_id="komendant-policji",
        status="completed",
        error=None,
        response="""
### Perspektywa
Priorytetem jest szybkie odciecie ruchu i zabezpieczenie strefy.

### Ryzyka
- Wtorny korek na objazdach.
- Brak koordynacji radiowej.

### Zalecenia
1. Zamknac wezly dojazdowe.
2. Wyznaczyc jeden objazd dla sluzb.
""",
    )

    assert summary.perspective.startswith("Priorytetem jest szybkie odciecie ruchu")
    assert summary.concerns[0] == "Wtorny korek na objazdach."
    assert summary.recommendations[0] == "Zamknac wezly dojazdowe."
    assert summary.urgency == "hours"


def test_build_scenario_version_from_orchestrator_report_extracts_variants() -> None:
    report = """
# Incydent: Karambol na S17

## Wariant strategiczny: Korytarz awaryjny bocznymi drogami

### Roznica wzgledem pozostalych
Najkrotszy czas dojscia do poszkodowanych.

### Harmonogram dzialan
#### 0-2 h
1. Zamknac wezly.
2. Wyslac PSP od strony wolnego odcinka.

#### 2-12 h
1. Ewakuowac rannych do szpitali.

#### 12-24 h
1. Udroznic pelna przepustowosc.

### Koszt - rozliczenie
- Suma szacunkowa: 75-140 tys. PLN

### Ryzyka wykonania i skutki uboczne
- Wtorny korek na objazdach.

### Zalety w tym kontekscie incydentu
- Najszybszy dostep do poszkodowanych.

### Konsekwencje odrzucenia tego wariantu
Opoznienie pierwszej pomocy o 30-60 min.

---

## Wariant strategiczny: Jeden bezpieczny punkt wjazdu

### Roznica wzgledem pozostalych
Silniejsza kontrola ruchu kosztem czasu.

### Harmonogram dzialan
#### 0-2 h
1. Zamknac wszystkie zjazdy.

#### 2-12 h
1. Triage na miejscu.

#### 12-24 h
1. Pelen raport i dochodzenie.

### Koszt - rozliczenie
- Suma szacunkowa: 128-215 tys. PLN

### Ryzyka wykonania i skutki uboczne
- Waskie gardlo na jednym wjezdzie.

### Zalety w tym kontekscie incydentu
- Latwiejsze dowodzenie.

### Konsekwencje odrzucenia tego wariantu
Slabsza kontrola strefy.

---

## Wariant strategiczny: LPR dla najciezszych przypadkow

### Roznica wzgledem pozostalych
Omija blokade ladowa dla najciezszych urazow.

### Harmonogram dzialan
#### 0-2 h
1. Zadysponowac LPR.

#### 2-12 h
1. Rotacje lotnicze.

#### 12-24 h
1. Standardowe udroznienie S17.

### Koszt - rozliczenie
- Suma szacunkowa: 90-170 tys. PLN

### Ryzyka wykonania i skutki uboczne
- Ograniczona liczba smiglowcow.

### Zalety w tym kontekscie incydentu
- Najszybszy transport do SOR.

### Konsekwencje odrzucenia tego wariantu
Wydluzenie ewakuacji pacjentow krytycznych.

# REKOMENDACJA ORCHESTRATORA

## Rekomendowany wariant
**Korytarz awaryjny bocznymi drogami**

## Uzasadnienie (fakty + liczby)
Najkrotszy czas pierwszej pomocy przy najnizszym koszcie.
"""

    version = build_scenario_version_from_orchestrator_report(
        report=report,
        run_id="run-1",
        incident_id="inc-1",
        fallback_confidence=0.78,
    )

    assert version is not None
    assert len(version.scenarios) == 3
    assert version.recommendation_label == "A"
    assert version.scenarios[0].is_recommended is True
    assert version.scenarios[0].estimated_cost == "75-140 tys. PLN"
    assert version.scenarios[1].actions.h2 == ["Zamknac wszystkie zjazdy."]
