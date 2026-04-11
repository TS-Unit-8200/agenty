#!/usr/bin/env python3
"""
Start a CrisisTwin agent, optionally with structured context, and print one assistant reply.

Requires ``ANTHROPIC_API_KEY`` or ``CGC_LLM_API_KEY`` (see ``agenty/.env.example``). Run from any directory::

    cd /path/to/agenty
    python examples/start_agent.py --list
    python examples/start_agent.py --agent orchestrator -m "Twój komunikat..."
"""

from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start an agent session and send one user message to the configured LLM API.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available agent ids (markdown stems under agents/) and exit.",
    )
    parser.add_argument(
        "--agent",
        default="orchestrator",
        metavar="ID",
        help="Agent id: filename stem without .md (default: orchestrator).",
    )
    parser.add_argument(
        "--message",
        "-m",
        default=(
            "Zaklasyfikuj krótko fikcyjny incydent: blackout w gminie, brak prądu od 2 godzin."
        ),
        help="First user message to the agent.",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Do not attach example AgentContext (system prompt = instructions only).",
    )
    args = parser.parse_args()

    from agenty import AgentContext, AgentRegistry, AgentRuntime

    registry = AgentRegistry()
    if args.list:
        for agent_id in registry.list_ids():
            title = registry.get(agent_id).title
            print(f"{agent_id}\t{title}")
        return 0

    context = None
    if not args.no_context:
        context = AgentContext(
            preamble="To jest uruchomienie przykładowe z examples/start_agent.py.",
            sections={
                "Incydent (przykład)": "Blackout — gmina demonstracyjna, priorytet WYSOKI, czas trwania ~2h.",
            },
        )

    try:
        runtime = AgentRuntime()
    except ValidationError as exc:
        print(
            "Could not load settings. Set ANTHROPIC_API_KEY or CGC_LLM_API_KEY in agenty/.env "
            "(copy from .env.example).",
            file=sys.stderr,
        )
        print(f"Detail: {exc}", file=sys.stderr)
        return 1

    try:
        session = runtime.start(args.agent, context=context)
    except KeyError as exc:
        print(exc, file=sys.stderr)
        print("Use --list to see valid agent ids.", file=sys.stderr)
        return 2

    print(f"Agent: {session.definition.title} ({session.definition.agent_id})\n")
    reply = session.say(args.message)
    print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
