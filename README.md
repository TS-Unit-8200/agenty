# agenty

Python runtime for **CrisisTwin** role agents: loads instruction markdown from `agents/`, talks to the **Comtegra GPU Cloud** LLM API (OpenAI-compatible), and reserves hooks for future **MCP** tooling.

- **LLM API reference:** [LLM inference API | Comtegra GPU Cloud](https://docs.cgc.comtegra.cloud/llm-api)  
- **Base URL (hosted):** `https://llm.comtegra.cloud/v1`  
- **Auth:** bearer token (`Authorization: Bearer …`), same as `api_key` in the OpenAI Python client.

## Requirements

- Python **3.12+**
- A CGC LLM API key (`cgc api-keys create --level LLM` — see docs above)

## Install

From this directory (`cyvil42/agenty/`):

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` and set your secret. **Do not commit** `.env`.

| Variable | Meaning |
|----------|---------|
| `CGC_LLM_BASE_URL` | API root (default: `https://llm.comtegra.cloud/v1`) |
| `CGC_LLM_API_KEY` | Bearer token from CGC |
| `CGC_LLM_CHAT_MODEL` | Model id, e.g. `llama3-8b` (must exist in your CGC account) |

Environment variables override `.env`. The library resolves `.env` from the **agenty project root** (next to `pyproject.toml`), not from your shell’s current working directory.

## Concepts

- **`AgentDefinition`** — One agent: id (markdown stem), title (from first heading), full instruction text.
- **`AgentRegistry`** — Scans `agents/*.md` and returns definitions by id.
- **`AgentContext`** — Optional structured context (`preamble`, `sections`, `metadata`) appended to the system message when the session starts.
- **`AgentRuntime`** — Shared LLM client + registry; **`start(agent_id, …)`** creates a session.
- **`AgentSession`** — One conversation; **`say(user_message)`** appends user + assistant messages and calls the API.
- **`MCPProvider`** — Protocol for future tool listing/execution (not wired into the chat loop yet).

## Starting an agent (code)

```python
from agenty import AgentContext, AgentRuntime

runtime = AgentRuntime()

session = runtime.start(
    "orchestrator",
    context=AgentContext(
        preamble="Tryb operacyjny — jedna tura odpowiedzi.",
        sections={
            "Incydent": "Blackout w gminie X, brak zasilania od 2 godzin, szpital na agregatach.",
        },
    ),
)

answer = session.say("Zaklasyfikuj incydent i wypisz 3 kluczowe pola zgodnie z instrukcją.")
print(answer)
```

List loaded agent ids:

```python
from agenty import AgentRegistry

registry = AgentRegistry()
print(registry.list_ids())
```

Override the chat model for one session:

```python
session = runtime.start("komendant-psp", context=None, model="llama3-8b")
```

## Command-line example

List agents (no API key required):

```bash
python examples/start_agent.py --list
```

One turn with default example context and message:

```bash
python examples/start_agent.py --agent orchestrator
```

Custom message and instructions only (no extra context block):

```bash
python examples/start_agent.py --agent marszalek-wojewodztwa -m "Opisz priorytety na pierwszą godzinę." --no-context
```

## Layout

| Path | Role |
|------|------|
| `agenty/` | Python package (`config`, `connection`, `context`, `agent`, `mcp`) |
| `agents/*.md` | System prompts / role specs |
| `examples/start_agent.py` | Runnable demo |
| `.env.example` | Template for secrets |
