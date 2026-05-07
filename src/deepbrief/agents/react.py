"""ReActAgent — the production-hardened agent loop.

This is the loop from notebook 01, factored out as a class so later
notebooks (multi-agent, HITL) can compose multiple agents.

Design choices:
- Uses `ToolRegistry` for dispatch (never raises on bad tool name/args)
- `parallel_tool_calls=True` so independent calls happen in one round-trip
- Captures a structured `trace` for observability (S8 §7)
- On `max_steps` exhaustion, forces a final answer with `tool_choice="none"`
  instead of raising (S8 §4.5: graceful termination)

Lecture reference: S8 §3 (loop), §4.5 (graceful termination).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from deepbrief.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    answer: str
    steps: int
    trace: list[dict[str, Any]] = field(default_factory=list)
    terminated_by: str = "natural"  # "natural" | "max_steps"


class ReActAgent:
    """Reason → Act → Observe loop, bounded by max_steps."""

    def __init__(
        self,
        registry: ToolRegistry,
        system_prompt: str,
        model: str = "gpt-4o-mini",
        max_steps: int = 10,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.registry = registry
        self.system_prompt = system_prompt
        self.model = model
        self.max_steps = max_steps
        self.client = client or AsyncOpenAI()

    async def run(self, user_msg: str) -> AgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_msg},
        ]
        trace: list[dict[str, Any]] = []

        for step in range(self.max_steps):
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.registry.get_openai_tools(),
                tool_choice="auto",
                parallel_tool_calls=True,
            )
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_unset=True))
            trace.append(
                {
                    "step": step,
                    "tokens": resp.usage.total_tokens,
                    "tool_calls": [tc.function.name for tc in (msg.tool_calls or [])],
                    "content": msg.content,
                }
            )

            # Natural termination
            if not msg.tool_calls:
                return AgentResult(
                    answer=msg.content or "",
                    steps=step + 1,
                    trace=trace,
                    terminated_by="natural",
                )

            # Execute tool calls in parallel — registry.execute() never raises
            async def _run_one(tc: Any) -> tuple[str, str]:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError as e:
                    content = json.dumps({"success": False, "error": f"Bad JSON: {e}"})
                    return tc.id, content
                result = await self.registry.execute(tc.function.name, args)
                return tc.id, result.model_dump_json()

            results = await asyncio.gather(*[_run_one(tc) for tc in msg.tool_calls])
            for tc_id, content in results:
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": content})

        # Max steps hit — force one final text answer
        messages.append(
            {
                "role": "user",
                "content": (
                    "Max steps reached. Provide your best answer based on what "
                    "you've learned. Do not call any more tools."
                ),
            }
        )
        final = await self.client.chat.completions.create(
            model=self.model, messages=messages, tool_choice="none"
        )
        return AgentResult(
            answer=final.choices[0].message.content or "",
            steps=self.max_steps,
            trace=trace,
            terminated_by="max_steps",
        )
