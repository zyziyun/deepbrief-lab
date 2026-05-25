"""MCPToolAdapter — bridges remote MCP tools to the BaseTool interface.

Lets a `ToolRegistry` hold a mix of native Python tools and remote MCP tools
without the agent loop knowing the difference.

Two design choices to call out:

1. **Per-call session vs persistent session.** This adapter opens a fresh
   MCP session on every call. Slightly higher overhead (~20-50 ms) than a
   shared persistent session, but **massively** simpler retry/cancel/error
   isolation. For first-iteration agents this is the right default.

2. **Namespacing.** Tool names are prefixed with the server name
   (`notes__save_note`, `cache__cache_get`). Without this, two MCP servers
   that both register a `search` tool would collide — and tool-name
   shadowing is a real attack vector..

Usage:
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    adapters = await discover_mcp_tools(
        url="http://localhost:8765/mcp", server_name="cache"
    )
    for a in adapters:
        registry.register(a)
"""

from __future__ import annotations

import json
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from deepbrief.tools.base import BaseTool, ToolResult


class MCPToolAdapter(BaseTool):
    """Adapts a single MCP tool (HTTP or stdio) to the BaseTool interface."""

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict,
        *,
        url: str | None = None,
        stdio_params: StdioServerParameters | None = None,
    ) -> None:
        if not (url or stdio_params):
            raise ValueError("MCPToolAdapter needs either url= or stdio_params=")
        self.name = f"{server_name}__{tool_name}"
        self.description = description or f"MCP tool: {tool_name}"
        # MCP servers don't always declare additionalProperties: False.
        # We patch it on so strict mode stays happy when the LLM sees this schema.
        self.parameters_schema = _strictify(input_schema)
        self._server_name = server_name
        self._tool_name = tool_name
        self._url = url
        self._stdio_params = stdio_params

    async def execute(self, **kwargs: Any) -> ToolResult:
        t0 = time.time()
        try:
            blocks, is_error = await self._call(kwargs)
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                input_args=kwargs,
                success=False,
                error=f"MCP transport error: {e}",
                latency_ms=int((time.time() - t0) * 1000),
            )

        # FastMCP returns multi-item results as separate content blocks
        # (one TextContent per list element). Parse each block individually
        # so list_notes() and friends return a real list of dicts, not a
        # blob of concatenated JSON.
        #   1 block  → unwrap to scalar / dict
        #   N blocks → keep as list
        output: Any
        if len(blocks) == 1:
            output = blocks[0]
        else:
            output = blocks

        return ToolResult(
            tool_name=self.name,
            input_args=kwargs,
            output=output if not is_error else None,
            success=not is_error,
            error=str(output) if is_error else None,
            latency_ms=int((time.time() - t0) * 1000),
        )

    async def _call(self, kwargs: dict) -> tuple[list[Any], bool]:
        """Open an MCP session, call the tool, parse each content block.

        Returns (parsed_blocks, is_error). Each block is either a parsed
        JSON value or its raw text — the caller decides how to combine
        single-vs-multi-block responses.
        """
        if self._url:
            async with streamablehttp_client(url=self._url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(self._tool_name, arguments=kwargs)
        else:
            async with stdio_client(self._stdio_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(self._tool_name, arguments=kwargs)

        blocks: list[Any] = []
        for c in r.content:
            text = getattr(c, "text", str(c))
            try:
                blocks.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                blocks.append(text)
        return blocks, bool(r.isError)


def _strictify(schema: dict) -> dict:
    """Ensure the schema can be sent with strict: True.

    OpenAI's strict mode requires additionalProperties: False AND every property
    listed in `required`. We patch in the former and trust MCP servers to declare
    `required` correctly (they almost always do).
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return schema
    out = dict(schema)
    out["additionalProperties"] = False
    if "properties" in out and "required" not in out:
        out["required"] = list(out["properties"].keys())
    return out


async def discover_http_tools(url: str, server_name: str) -> list[MCPToolAdapter]:
    """Connect to a Streamable HTTP MCP server, list its tools, return adapters."""
    async with streamablehttp_client(url=url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
    return [
        MCPToolAdapter(
            server_name=server_name,
            tool_name=t.name,
            description=t.description or "",
            input_schema=getattr(t, "inputSchema", {"type": "object", "properties": {}}),
            url=url,
        )
        for t in tools
    ]


async def discover_stdio_tools(
    stdio_params: StdioServerParameters, server_name: str
) -> list[MCPToolAdapter]:
    """Spawn a stdio MCP server, list its tools, return adapters.

    NOTE: each adapter created this way will spawn a *fresh* subprocess on every
    call, which is wasteful. For production stdio integration you'd hold one
    persistent session for the agent's lifetime. We keep it simple here.
    """
    async with stdio_client(stdio_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
    return [
        MCPToolAdapter(
            server_name=server_name,
            tool_name=t.name,
            description=t.description or "",
            input_schema=getattr(t, "inputSchema", {"type": "object", "properties": {}}),
            stdio_params=stdio_params,
        )
        for t in tools
    ]
