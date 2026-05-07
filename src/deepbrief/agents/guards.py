"""LoopGuard and CostMeter — runtime defenses against the two most expensive
agent failure modes.

These exist because **prompts can nudge behavior; prompts cannot enforce
budgets.** Even a perfectly-prompted agent can spiral when an upstream tool
returns ambiguous data — a real public incident reported a 150× cost overrun
on what was previously a 3-step task.

Lecture reference: S8 §4 (termination, defense in depth).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

# Pricing as of mid-2026 — keep in config and refresh on price changes.
# USD per token. Source: openai.com/api/pricing
PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":          {"prompt": 2.50 / 1e6, "completion": 10.00 / 1e6},
    "gpt-4o-mini":     {"prompt": 0.15 / 1e6, "completion": 0.60 / 1e6},
    "gpt-4.1":         {"prompt": 2.00 / 1e6, "completion": 8.00 / 1e6},
    "gpt-4.1-mini":    {"prompt": 0.40 / 1e6, "completion": 1.60 / 1e6},
    "o4-mini":         {"prompt": 1.10 / 1e6, "completion": 4.40 / 1e6},
}


class BudgetExceeded(Exception):
    """Raised by CostMeter when a per-run budget is exhausted."""


@dataclass
class CostMeter:
    """Tracks USD spent on LLM calls for a single agent run.

    Hard-stops when `budget_usd` is exceeded. Pair with per-user / per-tenant
    caps in Redis for production (see S8 §4.4).
    """

    budget_usd: float
    spent_usd: float = 0.0
    calls: list[dict] = field(default_factory=list)

    def charge(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        rate = PRICING.get(model)
        if rate is None:
            # Unknown model — log it but don't crash. Budget tracking degrades to "free".
            cost = 0.0
        else:
            cost = prompt_tokens * rate["prompt"] + completion_tokens * rate["completion"]
        self.spent_usd += cost
        self.calls.append(
            {"model": model, "prompt": prompt_tokens, "completion": completion_tokens, "cost_usd": cost}
        )
        if self.spent_usd > self.budget_usd:
            raise BudgetExceeded(
                f"Spent ${self.spent_usd:.4f} of ${self.budget_usd} budget on {model}"
            )
        return cost


@dataclass
class LoopGuard:
    """Detects literal repetition of the same (tool_name, arguments) call.

    Production agents stack this *before* tool execution. When a repeat is
    detected, return the canned message to the LLM as a tool result — the
    LLM almost always rephrases or gives up gracefully.

    Doesn't catch *semantic* repetition (`"weather in Paris"` vs
    `"current Paris weather"`). For that you'd need cosine similarity on
    embedded args, which is the next layer up.
    """

    repeat_threshold: int = 3
    history: list[str] = field(default_factory=list)

    def fingerprint(self, tool_name: str, args: dict) -> str:
        payload = f"{tool_name}:{json.dumps(args, sort_keys=True)}".encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    def is_looping(self, tool_name: str, args: dict) -> bool:
        fp = self.fingerprint(tool_name, args)
        self.history.append(fp)
        return self.history.count(fp) >= self.repeat_threshold
