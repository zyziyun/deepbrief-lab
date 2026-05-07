"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from deepbrief.tools.base import BaseTool, ToolResult


class EchoTool(BaseTool):
    """Predictable tool used as a test stand-in."""

    name = "echo"
    description = "Echo back the input. Test fixture only."
    parameters_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, text: str) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            input_args={"text": text},
            output={"echoed": text},
            success=True,
        )


class ExplodingTool(BaseTool):
    """Tool that always raises — used to verify the registry catches it."""

    name = "explode"
    description = "Always raises. Test fixture only."
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self) -> ToolResult:
        raise RuntimeError("kaboom")


@pytest.fixture
def echo_tool() -> EchoTool:
    return EchoTool()


@pytest.fixture
def exploding_tool() -> ExplodingTool:
    return ExplodingTool()
