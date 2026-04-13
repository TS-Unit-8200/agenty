# Agenty

`agenty` to backend orkiestracji dla Crisis OS. Odpowiada za Radę Agentów, hierarchię decyzyjną, uruchamianie workflow incydentów, generowanie rekomendacji i scenariuszy oraz integrację z warstwą telefonicznego pozyskiwania danych.

## Co robi ten projekt

- uruchamia agentów rolowych dla incydentu,
- prowadzi workflow orkiestracji i zapisuje jego stan w MongoDB,
- pobiera dane organizacji, incydentów i zasobów z `civil42-frontend`,
- korzysta z modeli LLM do odpowiedzi agentów i syntezy,
- integruje telefoniczny tool-use przez `ai-backend`,
- wystawia API dla frontendu i innych klientów.

## Stack

- Python 3.12+
- FastAPI
- Uvicorn
- PyMongo
- LangGraph
- OpenAI-compatible clients

## Wymagania

- Python 3.12+
- dostęp do MongoDB
- działający `civil42-frontend`
- opcjonalnie działający `ai-backend` do telefonii
- klucz do Anthropic albo Comtegra CGC

## Instalacja

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Na systemach Unix:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Konfiguracja

1. Skopiuj plik:

```bash
cp .env.example .env
```

2. Uzupełnij zmienne środowiskowe.

3. Uruchom API:

```bash
uvicorn agenty.api.server:app_factory --factory --reload --port 8080
```

## Najważniejsze zmienne

### Provider LLM

Masz dwie ścieżki:

- Anthropic - jeśli `ANTHROPIC_API_KEY` jest ustawiony, ta ścieżka ma priorytet,
- Comtegra CGC - fallback albo główny provider w trybie awaryjnym.

### MongoDB

- `DATABASE_URL`
- `MONGODB_DATABASE`

To źródło trwałego stanu workflow, wyników agentów i wpisów orkiestracji.

### Integracja z frontendem

- `NEXTJS_API_BASE_URL`
- `NEXTJS_API_TOKEN`

Backend używa tych zmiennych do pobierania danych Civil42, w tym zasobów incydentu.

### Integracja telefoniczna

- `PHONE_AGENT_ENABLED`
- `PHONE_AGENT_BASE_URL`
- `PHONE_AGENT_API_TOKEN`
- `PHONE_AGENT_DEFAULT_PHONE_NUMBER`

`PHONE_AGENT_DEFAULT_PHONE_NUMBER` to numer zapasowy dla ścieżek, które tego wymagają. W nowym flow telefonicznym preferowane są zasoby przypisane do incydentu.

## Najważniejsze endpointy

- `POST /orchestrations`
- `POST /orchestrations/intake`
- `POST /orchestrations/report`
- `POST /orchestrations/{run_id}/resume`
- `GET /orchestrations/{run_id}`
- `GET /orchestrations/{run_id}/result`

## Logi

Projekt zapisuje dwa typy logów:

- `logs/orchestration-trace.log` - pełny ślad techniczny,
- `logs/orchestration-pretty.log` - czytelniejszy log operacyjny.

Ścieżki są konfigurowalne przez:

- `ORCHESTRATION_LOG_FILE`
- `ORCHESTRATION_HUMAN_LOG_FILE`

## Testy

```bash
pytest
```

## Architektura w skrócie

- `agenty/api` - FastAPI i endpointy publiczne
- `agenty/orchestration` - silnik workflow, repozytorium i modele
- `agenty/mcp_gateway` - integracje narzędziowe i gateway MCP
- `agents/` - prompty i definicje ról
- `tests/` - testy jednostkowe i integracyjne

## Tryb awaryjny

Projekt wspiera działanie z wykorzystaniem Comtegra CGC jako ścieżki zapasowej dla modeli. Dzięki temu orkiestracja może działać także wtedy, gdy podstawowy provider nie jest dostępny.

## Powiązane repozytoria

- `civil42-frontend` - interfejs i model danych Civil42
- `ai-backend` - telefonia AI i ekstrakcja danych z rozmów
