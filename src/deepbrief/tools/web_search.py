"""WebSearchTool — wraps Tavily's search API as a BaseTool.

Tavily is one of the few search APIs with a meaningful free tier (1000/month).
For students without a key we ship a `MockSearchTool` that returns canned
results, so notebook 06+ runs without a Tavily account.

Lecture reference: S8 §6.3 (tool design).
"""

from __future__ import annotations

import os
from typing import Any

from deepbrief.tools.base import BaseTool, ToolResult


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web for up-to-date information on a topic. Returns the top "
        "results with titles, URLs, and short content snippets. "
        "Call this when the user asks about current events, recent data, or "
        "topics that the model's training data may be stale on. "
        "Do NOT call this for definitional questions you can answer from "
        "general knowledge (e.g., 'what is HTTP?')."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (5-15 words is the sweet spot)",
            },
            "max_results": {
                "type": "integer",
                "description": "How many results to return, 1 to 10",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query", "max_results"],
        "additionalProperties": False,
    }

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "TAVILY_API_KEY not set. Get one free at https://tavily.com "
                "or use MockSearchTool for offline testing."
            )

    async def execute(self, query: str, max_results: int = 5) -> ToolResult:
        from tavily import AsyncTavilyClient

        client = AsyncTavilyClient(api_key=self.api_key)
        try:
            r = await client.search(query=query, max_results=max_results)
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                input_args={"query": query, "max_results": max_results},
                success=False,
                error=f"Tavily error: {e}",
            )
        hits = [
            {"title": h["title"], "url": h["url"], "snippet": h["content"][:300]}
            for h in r.get("results", [])
        ]
        return ToolResult(
            tool_name=self.name,
            input_args={"query": query, "max_results": max_results},
            output={"results": hits, "count": len(hits)},
            success=True,
        )


class MockSearchTool(BaseTool):
    """Offline stand-in for WebSearchTool — returns canned results.

    Useful for testing the agent loop without burning API quota.
    """

    name = "web_search"
    description = WebSearchTool.description
    parameters_schema = WebSearchTool.parameters_schema

    _CANNED: dict[str, list[dict[str, Any]]] = {
        "default": [
            {
                "title": "Mock result 1",
                "url": "https://example.com/mock-1",
                "snippet": "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
            },
            {
                "title": "Mock result 2",
                "url": "https://example.com/mock-2",
                "snippet": "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
            },
        ],
    }

    async def execute(self, query: str, max_results: int = 5) -> ToolResult:
        hits = self._CANNED.get(query.lower(), self._CANNED["default"])[:max_results]
        return ToolResult(
            tool_name=self.name,
            input_args={"query": query, "max_results": max_results},
            output={"results": hits, "count": len(hits), "_mock": True},
            success=True,
        )
