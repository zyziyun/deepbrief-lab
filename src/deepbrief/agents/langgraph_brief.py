"""LangGraph rewrite of the deepbrief deep-research pipeline.

The same problem as `coordinator.py` (decompose → parallel research → synthesize),
re-implemented on LangGraph's StateGraph runtime. This is the production-grade
replacement for the hand-rolled A2A coordinator used in notebook 07.

What LangGraph gives us that the hand-rolled version doesn't:
- **TypedDict state with reducers** — `findings` field aggregates in parallel
  branches without silent data loss.
- **Dynamic fan-out via `Send`** — sub-question count is decided at runtime,
  not compile time.
- **Supersteps with all-or-nothing semantics** — if one researcher crashes,
  the others' results are preserved on the next replay.
- **Free state inspection** — `app.get_state_history(config)` gives you the
  full audit trail without writing any logging code.

Checkpointer and `interrupt()` are NOT used here — those land in notebook 08
extension where we upgrade the HITL gate. This module focuses on the
multi-agent decomposition pattern itself.

Architecture:
    decompose ──Send(research, payload=q)──▶ research ──┐
                Send(research, payload=q)──▶ research ──┤
                Send(research, payload=q)──▶ research ──┤
                                                        ▼
                                               synthesize ──▶ END

combined production shape).
"""

from __future__ import annotations

import json
import logging
import operator
import os
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from openai import AsyncOpenAI

from deepbrief.agents.react import ReActAgent
from deepbrief.tools.fetch_url import FetchURLTool
from deepbrief.tools.registry import ToolRegistry
from deepbrief.tools.web_search import MockSearchTool, WebSearchTool

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────


class Finding(TypedDict):
    """One researcher's contribution. Keyed by question for traceability."""

    question: str
    summary: str
    sources: list[str]


class ResearchState(TypedDict):
    """The shared state object passed between nodes.

    Three field semantics:
    - `topic`, `draft` — single-writer, default (overwrite) reducer.
    - `sub_questions` — written once by decompose; default reducer.
    - `findings` — written by N parallel research nodes; needs `operator.add`
      so the lists CONCAT instead of clobber. This is the #1 LangGraph beginner
      gotcha.
    """

    topic: str
    sub_questions: list[str]
    findings: Annotated[list[Finding], operator.add]
    draft: str | None


# ─────────────────────────────────────────────────────────────────────────────
# Prompts (kept in module to make the notebook easier to read)
# ─────────────────────────────────────────────────────────────────────────────


DECOMPOSE_PROMPT = """You decompose research topics into focused sub-questions.

Given a TOPIC, output JSON:
{{
  "subquestions": ["<focused question 1>", "<focused question 2>", ...]
}}

Rules:
- 3 to 4 sub-questions (keep it tight for the demo).
- Each is independently researchable.
- Cover different facets (what / state / examples / criticism).

TOPIC: {topic}
"""


RESEARCHER_SYSTEM = """You are a focused research agent. Given a single sub-question:

1. Use `web_search` once or twice (max_results=3).
2. Use `fetch_url` on the 1-2 most promising results.
3. Return a 2-4 sentence summary plus the URLs you fetched.

Be concise. The orchestrator merges your output with other researchers'.

# Output format (final answer only)
SUMMARY: <2-4 sentences>

SOURCES:
- <url 1>
- <url 2>
"""


SYNTHESIZE_PROMPT = """You merge research notes into one Markdown brief.

TOPIC: {topic}

NOTES:
{notes}

Output:
- # <title>
- 2-3 short sections with ## headings
- Inline [1], [2] citations referring to a # Sources section at the bottom
- Under 350 words total
"""


# ─────────────────────────────────────────────────────────────────────────────
# Nodes — pure functions of (state) -> partial state update
# ─────────────────────────────────────────────────────────────────────────────


def _build_researcher_registry() -> ToolRegistry:
    """Build a ToolRegistry with web_search + fetch_url, falling back to mock."""
    registry = ToolRegistry()
    if os.getenv("TAVILY_API_KEY"):
        registry.register(WebSearchTool())
    else:
        registry.register(MockSearchTool())
    registry.register(FetchURLTool())
    return registry


def _client() -> AsyncOpenAI:
    return AsyncOpenAI()


def _model() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


async def decompose_node(state: ResearchState) -> dict:
    """LLM call: turn the topic into 3-4 sub-questions.

    Returns a partial state update — only `sub_questions` is written here.
    """
    resp = await _client().chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": DECOMPOSE_PROMPT.format(topic=state["topic"])}],
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    subqs = data.get("subquestions", [])[:4]
    logger.info("decompose: %d sub-questions", len(subqs))
    return {"sub_questions": subqs}


def plan_research(state: ResearchState) -> list[Send]:
    """Conditional edge: dynamic fan-out via Send API.

    Returns one Send per sub-question. Each Send creates a parallel branch
    of the `research` node with its own payload. This is map-reduce inside
    LangGraph — the count is decided at runtime, not at graph-build time.

    """
    return [
        Send("research", {"question": q})
        for q in state["sub_questions"]
    ]


async def research_node(payload: dict) -> dict:
    """Run a ReAct loop for ONE sub-question.

    Note the signature: this node receives the Send payload (a dict with
    `question`), NOT the full state. That's how Send broadcasts different
    work to each parallel branch.

    Returns `findings` as a single-element list. The reducer
    (`operator.add`) handles concatenation across the parallel branches.
    """
    question = payload["question"]

    agent = ReActAgent(
        registry=_build_researcher_registry(),
        system_prompt=RESEARCHER_SYSTEM,
        max_steps=6,
    )
    result = await agent.run(question)

    # Parse the SUMMARY / SOURCES sections out of the answer (best-effort).
    answer = result.answer or ""
    summary, sources = _parse_researcher_output(answer)

    return {
        "findings": [
            Finding(question=question, summary=summary, sources=sources)
        ]
    }


def _parse_researcher_output(text: str) -> tuple[str, list[str]]:
    """Pull the SUMMARY and SOURCES sections out of a researcher response."""
    summary = text
    sources: list[str] = []
    if "SOURCES:" in text:
        summary_part, sources_part = text.split("SOURCES:", 1)
        summary = summary_part.replace("SUMMARY:", "").strip()
        for line in sources_part.splitlines():
            line = line.strip().lstrip("- ").strip()
            if line.startswith(("http://", "https://")):
                sources.append(line)
    elif "SUMMARY:" in text:
        summary = text.split("SUMMARY:", 1)[1].strip()
    return summary[:600], sources[:3]


async def synthesize_node(state: ResearchState) -> dict:
    """LLM call: merge all findings into one Markdown brief.

    Reads the aggregated `findings` (already concatenated by the reducer)
    and produces the final draft.
    """
    notes_text = "\n\n---\n\n".join(
        f"## Sub-question {i+1}: {f['question']}\n"
        f"{f['summary']}\n\n"
        f"Sources:\n" + "\n".join(f"- {s}" for s in f["sources"])
        for i, f in enumerate(state["findings"])
    )

    resp = await _client().chat.completions.create(
        model=_model(),
        messages=[
            {
                "role": "user",
                "content": SYNTHESIZE_PROMPT.format(topic=state["topic"], notes=notes_text),
            }
        ],
    )
    draft = resp.choices[0].message.content or ""
    return {"draft": draft}


# ─────────────────────────────────────────────────────────────────────────────
# Graph factory
# ─────────────────────────────────────────────────────────────────────────────


def build_brief_graph() -> StateGraph:
    """Construct the deep-research StateGraph.

    Returns the *un-compiled* graph so callers can attach checkpointers
    or alter the topology (e.g., notebook 08 adds a review node).

    Topology:

        START
          │
          ▼
        decompose
          │
          ▼ (conditional edge — plan_research returns N Sends)
        research × N   ← parallel superstep, all share `findings` via reducer
          │
          ▼ (static edge)
        synthesize
          │
          ▼
         END
    """
    graph = StateGraph(ResearchState)
    graph.add_node("decompose", decompose_node)
    graph.add_node("research", research_node)
    graph.add_node("synthesize", synthesize_node)

    graph.add_edge(START, "decompose")
    # Dynamic fan-out: the conditional edge function returns list[Send],
    # not a string. LangGraph dispatches each Send as its own branch.
    graph.add_conditional_edges("decompose", plan_research, ["research"])
    graph.add_edge("research", "synthesize")
    graph.add_edge("synthesize", END)

    return graph


def build_brief_app(checkpointer=None):
    """Convenience: build + compile the graph in one call.

    Pass `checkpointer=SqliteSaver(...)` for the notebook 08 extension
    where we add durable HITL via `interrupt()`. For notebook 07b we
    leave it None and rely on in-memory ephemeral state.
    """
    graph = build_brief_graph()
    return graph.compile(checkpointer=checkpointer) if checkpointer else graph.compile()
