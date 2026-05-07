"""Researcher agent — runs as an A2A server, researches one sub-question.

Internally uses a `ReActAgent` with web_search + fetch_url tools. Hooks into
A2A so the coordinator can delegate to it via `tasks/send`.

Run it:
    python -m deepbrief.agents.researcher --port 9001
    # exposes: GET  http://localhost:9001/.well-known/agent-card.json
    #          POST http://localhost:9001/a2a
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn
from dotenv import load_dotenv

from deepbrief.a2a.agent_card import researcher_card
from deepbrief.a2a.server import make_a2a_app
from deepbrief.agents.react import ReActAgent
from deepbrief.tools.fetch_url import FetchURLTool
from deepbrief.tools.registry import ToolRegistry
from deepbrief.tools.web_search import MockSearchTool, WebSearchTool


SYSTEM_PROMPT = """You are a focused research agent. Given a single sub-question, produce:

1. A 2-4 sentence factual summary
2. Up to 3 source URLs you actually fetched

# Workflow
- Use `web_search` once or twice to find candidates (max_results=3-5)
- Use `fetch_url` on the 1-2 most promising results
- Be concise. The orchestrator will combine your output with other researchers'.

# Constraints
- At most 2 web_search calls
- At most 2 fetch_url calls
- Do not editorialize. Stick to what the sources say.

# Output format (final answer only)
SUMMARY: <2-4 sentences>

SOURCES:
- <url 1>
- <url 2>
"""


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    if os.getenv("TAVILY_API_KEY"):
        registry.register(WebSearchTool())
    else:
        registry.register(MockSearchTool())
    registry.register(FetchURLTool())
    return registry


def make_handler():
    """Return an async handler that ReActAgent-runs each incoming task."""
    registry = _build_registry()
    agent = ReActAgent(
        registry=registry,
        system_prompt=SYSTEM_PROMPT,
        max_steps=8,
    )

    async def handle(text: str) -> str:
        result = await agent.run(text)
        return result.answer

    return handle


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    public_url = f"http://localhost:{args.port}"
    card = researcher_card(public_url)
    app = make_a2a_app(card, make_handler())

    print(f"Researcher up at {public_url}")
    print(f"  card: {public_url}/.well-known/agent-card.json")
    print(f"  rpc:  {public_url}/a2a")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
