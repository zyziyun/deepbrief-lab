"""BaseTool and ToolResult — the contract every tool implements.

This is the abstract base every concrete tool subclasses. The `execute()`
method is async-only by design: I/O-bound tools (HTTP calls, DB queries)
need to be non-blocking so the agent can run them in parallel.

`ToolResult` is the uniform return shape — every tool returns one of these.
Tools NEVER raise; failures become `ToolResult(success=False, error=...)`
so the agent loop can pass the error back to the LLM as an observation.
See S8 §3.5 and §6.3 for the rationale.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Uniform return shape for every tool. Never raises — error is a field."""

    tool_name: str = Field(..., description="Name of the executed tool")
    input_args: dict = Field(default_factory=dict, description="Args the tool was called with")
    output: Any = Field(default=None, description="Tool output (varies by tool)")
    success: bool = Field(..., description="Whether execution succeeded")
    error: str | None = Field(default=None, description="Error message if not success")
    latency_ms: int = Field(default=0, description="Execution time in ms")


class BaseTool(ABC):
    """Abstract base — every tool subclasses this.

    Subclasses MUST set:
        name: str                    # unique identifier
        description: str             # what + when-to-call + when-NOT-to-call
        parameters_schema: dict      # JSON Schema (with additionalProperties: False for strict)

    Subclasses MUST implement:
        async def execute(self, **kwargs) -> ToolResult
    """

    name: str
    description: str
    parameters_schema: dict

    def to_openai_function(self) -> dict:
        """Convert to OpenAI function-calling schema with strict mode enabled.

        `strict: True` forces the LLM to emit args that conform to the schema
        at decode time (constrained decoding), not by validation after the fact.
        Eliminates ~3-5% of agent bugs caused by malformed args.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
                "strict": True,
            },
        }

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Run the tool. MUST return a ToolResult — never raise."""
        ...

    async def _execute_with_timing(self, **kwargs: Any) -> ToolResult:
        """Internal helper: wraps execute() and fills latency_ms."""
        t0 = time.time()
        try:
            result = await self.execute(**kwargs)
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                input_args=kwargs,
                success=False,
                error=f"{type(e).__name__}: {e}",
                latency_ms=int((time.time() - t0) * 1000),
            )
        if result.latency_ms == 0:
            result.latency_ms = int((time.time() - t0) * 1000)
        return result
