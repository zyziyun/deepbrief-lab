"""Tests for the tool layer: BaseTool, ToolResult, ToolRegistry, MCP adapter helpers."""

from __future__ import annotations

import pytest

from deepbrief.tools.base import BaseTool, ToolResult
from deepbrief.tools.mcp_adapter import _strictify
from deepbrief.tools.registry import ToolRegistry
from deepbrief.tools.web_search import MockSearchTool


# ─────────────────────────────────────────────────────────────────────────────
# ToolResult
# ─────────────────────────────────────────────────────────────────────────────


class TestToolResult:
    def test_minimal_success(self):
        r = ToolResult(tool_name="x", success=True)
        assert r.tool_name == "x"
        assert r.success is True
        assert r.input_args == {}
        assert r.output is None
        assert r.error is None
        assert r.latency_ms == 0

    def test_failure_carries_error(self):
        r = ToolResult(tool_name="x", success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"


# ─────────────────────────────────────────────────────────────────────────────
# BaseTool.to_openai_function
# ─────────────────────────────────────────────────────────────────────────────


class TestBaseToolSchema:
    def test_emits_strict_true(self, echo_tool):
        schema = echo_tool.to_openai_function()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "echo"
        assert schema["function"]["strict"] is True
        assert schema["function"]["parameters"]["additionalProperties"] is False

    def test_description_is_carried(self, echo_tool):
        schema = echo_tool.to_openai_function()
        assert schema["function"]["description"] == echo_tool.description


# ─────────────────────────────────────────────────────────────────────────────
# ToolRegistry
# ─────────────────────────────────────────────────────────────────────────────


class TestToolRegistry:
    def test_register_and_get(self, echo_tool):
        r = ToolRegistry()
        r.register(echo_tool)
        assert r.get("echo") is echo_tool
        assert r.get("missing") is None
        assert r.list_tools() == ["echo"]

    def test_double_register_collides(self, echo_tool):
        r = ToolRegistry()
        r.register(echo_tool)
        with pytest.raises(ValueError, match="already registered"):
            r.register(echo_tool)

    def test_get_openai_tools_shape(self, echo_tool):
        r = ToolRegistry()
        r.register(echo_tool)
        schemas = r.get_openai_tools()
        assert len(schemas) == 1
        assert schemas[0]["function"]["strict"] is True

    @pytest.mark.asyncio
    async def test_execute_success_path(self, echo_tool):
        r = ToolRegistry()
        r.register(echo_tool)
        result = await r.execute("echo", {"text": "hi"})
        assert result.success is True
        assert result.output == {"echoed": "hi"}
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, echo_tool):
        r = ToolRegistry()
        r.register(echo_tool)
        result = await r.execute("nope", {})
        assert result.success is False
        assert "Unknown tool" in result.error

    @pytest.mark.asyncio
    async def test_bad_args_returns_error(self, echo_tool):
        r = ToolRegistry()
        r.register(echo_tool)
        # echo wants `text`; we pass `wrong` — should NOT raise
        result = await r.execute("echo", {"wrong": "value"})
        assert result.success is False
        # error message format is implementation detail; assert it mentions the type
        assert "TypeError" in result.error or "argument" in result.error.lower()

    @pytest.mark.asyncio
    async def test_tool_exception_is_caught(self, exploding_tool):
        r = ToolRegistry()
        r.register(exploding_tool)
        result = await r.execute("explode", {})
        assert result.success is False
        assert "kaboom" in result.error


# ─────────────────────────────────────────────────────────────────────────────
# MockSearchTool — verify the offline fallback shape
# ─────────────────────────────────────────────────────────────────────────────


class TestMockSearchTool:
    @pytest.mark.asyncio
    async def test_returns_canned_results(self):
        tool = MockSearchTool()
        result = await tool.execute(query="anything", max_results=2)
        assert result.success is True
        assert result.output["count"] == 2
        assert result.output["_mock"] is True
        assert all("url" in hit for hit in result.output["results"])


# ─────────────────────────────────────────────────────────────────────────────
# MCP adapter — _strictify schema patch
# ─────────────────────────────────────────────────────────────────────────────


class TestStrictify:
    def test_object_schema_gets_additional_properties_false(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        result = _strictify(schema)
        assert result["additionalProperties"] is False
        assert result["required"] == ["q"]

    def test_preserves_explicit_required(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a"],
        }
        result = _strictify(schema)
        assert result["required"] == ["a"]    # not overwritten
        assert result["additionalProperties"] is False

    def test_non_object_schema_passes_through(self):
        # Not all MCP tools have object schemas at the top level — defensive
        schema = {"type": "string"}
        assert _strictify(schema) == schema
