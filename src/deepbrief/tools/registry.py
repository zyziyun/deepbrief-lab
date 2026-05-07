"""ToolRegistry — the agent's view of what tools exist.

The registry is a single source of truth: register tools at startup, the
agent reads schemas via `get_openai_tools()` and dispatches via `execute()`.

`execute()` NEVER raises. Unknown tool names, bad arguments, and tool
exceptions all become `ToolResult(success=False, error=...)` returned to
the LLM as an observation. The LLM almost always self-corrects.

Lecture reference: S8 §3.5.
"""

from __future__ import annotations

import logging
from typing import Any

from deepbrief.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Holds tools by name and dispatches to them safely."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered — namespace collision")
        self._tools[tool.name] = tool
        logger.debug("registered tool %s", tool.name)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools)

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Return all tool schemas in OpenAI's `tools` parameter format."""
        return [t.to_openai_function() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name. NEVER raises — always returns ToolResult."""
        tool = self._tools.get(name)
        if tool is None:
            # Hallucinated tool name — return error to LLM
            return ToolResult(
                tool_name=name,
                input_args=arguments,
                success=False,
                error=f"Unknown tool: {name}. Available: {', '.join(self._tools)}",
            )
        try:
            return await tool._execute_with_timing(**arguments)
        except TypeError as e:
            # Argument mismatch (missing/extra/wrong type)
            return ToolResult(
                tool_name=name,
                input_args=arguments,
                success=False,
                error=f"Invalid arguments for {name}: {e}",
            )
