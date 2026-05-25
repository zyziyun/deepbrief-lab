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


# ─────────────────────────────────────────────────────────────────────────────
# MCP adapter — multi-block content parsing (regression for notebook 06 bug)
# ─────────────────────────────────────────────────────────────────────────────


class TestMCPAdapterMultiBlock:
    """Regression test for the notebook 06 §4 bug:

    FastMCP returns multi-item results as separate TextContent blocks (one
    per list element). The adapter must parse each block individually so
    callers like `list_notes` get a real list of dicts back, not a blob of
    concatenated JSON that fails to parse and falls back to raw string.
    """

    @pytest.mark.asyncio
    async def test_multi_block_returns_list(self):
        from deepbrief.tools.mcp_adapter import MCPToolAdapter

        adapter = MCPToolAdapter(
            server_name="notes", tool_name="list_notes",
            description="x", input_schema={"type": "object", "properties": {}},
            url="http://test/mcp",
        )

        # Monkeypatch _call to simulate FastMCP's multi-block list response
        async def fake_call(kwargs):
            return [
                {"id": "abc", "title": "Note 1"},
                {"id": "def", "title": "Note 2"},
                {"id": "ghi", "title": "Note 3"},
            ], False

        adapter._call = fake_call
        result = await adapter.execute()
        assert result.success is True
        assert isinstance(result.output, list)
        assert len(result.output) == 3
        # Iterating output must give dicts, not characters
        ids = [n["id"] for n in result.output]
        assert ids == ["abc", "def", "ghi"]

    @pytest.mark.asyncio
    async def test_single_block_unwraps(self):
        """Single-block responses (save_note, cache_set) should unwrap to scalar/dict."""
        from deepbrief.tools.mcp_adapter import MCPToolAdapter

        adapter = MCPToolAdapter(
            server_name="notes", tool_name="save_note",
            description="x", input_schema={"type": "object", "properties": {}},
            url="http://test/mcp",
        )

        async def fake_call(kwargs):
            return [{"note_id": "xyz", "title": "T"}], False

        adapter._call = fake_call
        result = await adapter.execute()
        assert result.success is True
        # Single block → unwrapped to dict, NOT a list of one
        assert result.output == {"note_id": "xyz", "title": "T"}


# ─────────────────────────────────────────────────────────────────────────────
# Coordinator — DECOMPOSE_PROMPT JSON-brace escaping (regression for notebook 07)
# ─────────────────────────────────────────────────────────────────────────────


class TestCoordinatorPromptFormat:
    """Regression test for the notebook 07 §7 KeyError:

    DECOMPOSE_PROMPT contains a literal JSON example with { and } — these
    must be escaped to {{ and }} or str.format() crashes when it sees them
    as placeholder syntax.
    """

    def test_decompose_prompt_formats_without_keyerror(self):
        from deepbrief.agents.coordinator import DECOMPOSE_PROMPT

        # Must not raise KeyError on the JSON keywords
        formatted = DECOMPOSE_PROMPT.format(topic="WebGPU adoption 2026")
        assert "WebGPU adoption 2026" in formatted
        # The literal JSON example survived as a single brace each
        assert '{\n  "subquestions"' in formatted
        assert "]\n}" in formatted

    def test_synthesize_prompt_formats_with_topic_and_notes(self):
        from deepbrief.agents.coordinator import SYNTHESIZE_PROMPT

        formatted = SYNTHESIZE_PROMPT.format(topic="X", notes="some notes here")
        assert "X" in formatted
        assert "some notes here" in formatted
