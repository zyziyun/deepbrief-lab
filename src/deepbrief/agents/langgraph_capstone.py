"""LangGraph capstone — adds `interrupt()` HITL + checkpointer to the brief graph.

This module extends `langgraph_brief.py` with a `review` node that pauses the
graph using LangGraph's `interrupt()` primitive. The whole point is that
between `interrupt()` and the human's `Command(resume=...)` answer, the
process can die / restart / redeploy and execution still picks up from the
last checkpoint.

The S8 hand-rolled `ApprovalStore` does the same job with a file-backed
polling queue. This is the LangGraph-native replacement — same functionality,
plus the checkpointer also gives us:

- Multi-turn state isolated per `thread_id`
- Full state history via `app.get_state_history(config)` — time travel
- Crash recovery mid-superstep

Architecture diff vs `langgraph_brief.py`:

    decompose ──▶ research × N ──▶ synthesize ──▶ review (interrupt) ──▶ END
                                       ▲                │
                                       └────────────────┘
                                       if reviewer says "revise"

Lecture references: S9 §3.1 (Checkpointer), §3.2 (interrupt), §3.3 (time travel).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, TypedDict

import operator
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from deepbrief.agents.langgraph_brief import (
    Finding,
    decompose_node,
    plan_research,
    research_node,
    synthesize_node,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Capstone state — same as ResearchState plus reviewer fields
# ─────────────────────────────────────────────────────────────────────────────


class CapstoneState(TypedDict):
    """Extended state for the HITL capstone.

    Adds `approved` and `reviewer_notes` to the base ResearchState. The
    `revision_count` is bounded — we don't want the LLM and the reviewer
    bouncing forever.
    """

    topic: str
    sub_questions: list[str]
    findings: Annotated[list[Finding], operator.add]
    draft: str | None
    # HITL-specific
    approved: bool
    reviewer_notes: str | None
    revision_count: int


# ─────────────────────────────────────────────────────────────────────────────
# The review node — where `interrupt()` lives
# ─────────────────────────────────────────────────────────────────────────────


MAX_REVISIONS = 2


def review_node(state: CapstoneState) -> dict:
    """Pause the graph for human review.

    `interrupt(payload)` does four things:
        1. checkpoints the current state to whatever backend we wired in
        2. returns control to the caller with an `__interrupt__` marker
        3. allows the process to exit, restart, redeploy (state is durable)
        4. when `app.invoke(Command(resume=value), config=config)` is called
           later, this `interrupt(...)` call returns `value`

    See S9 §3.2.

    The payload format is by convention — there's no required shape. We use
    a structured dict so a UI can render approve / reject / edit affordances
    against it.
    """
    revision_count = state.get("revision_count", 0)
    if revision_count >= MAX_REVISIONS:
        # Don't loop forever — auto-approve after MAX_REVISIONS rounds
        logger.warning("Max revisions hit; auto-approving")
        return {"approved": True, "reviewer_notes": "auto-approved (max revisions)"}

    decision = interrupt(
        {
            "kind": "approve_brief",
            "topic": state["topic"],
            "draft_preview": (state["draft"] or "")[:600],
            "revision_count": revision_count,
            "question": "approve / reject / revise — return one of those strings, or a dict {'action': 'revise', 'notes': '...'}",
        }
    )
    # When resume() is called, this line runs and `decision` holds the response.
    logger.info("Review resumed with decision: %r", decision)

    if isinstance(decision, dict):
        action = decision.get("action", "approve")
        notes = decision.get("notes")
    else:
        action = str(decision).lower().strip()
        notes = None

    if action == "revise":
        return {
            "approved": False,
            "reviewer_notes": notes,
            "revision_count": revision_count + 1,
        }
    if action == "reject":
        return {"approved": False, "reviewer_notes": notes or "rejected"}
    # default: approve
    return {"approved": True, "reviewer_notes": notes}


def needs_revision(state: CapstoneState) -> str:
    """Conditional edge: approved → END; revise (not rejected) → synthesize."""
    if state["approved"]:
        return END
    if state.get("reviewer_notes") == "rejected":
        return END
    return "synthesize"


# ─────────────────────────────────────────────────────────────────────────────
# Graph factory
# ─────────────────────────────────────────────────────────────────────────────


def build_capstone_graph() -> StateGraph:
    """Construct the HITL capstone graph.

    Same shape as `langgraph_brief` but with `review` after `synthesize`
    and a back-edge from `review` to `synthesize` for revision rounds.

    Topology:

        START
          │
          ▼
        decompose
          │
          ▼ (Send fan-out — N branches)
        research × N
          │
          ▼ (static edge)
        synthesize ◀──────┐
          │               │
          ▼               │
        review ───────────┘ (if not approved and not rejected)
          │
          ▼ (if approved or rejected)
         END
    """
    graph = StateGraph(CapstoneState)
    graph.add_node("decompose", decompose_node)
    graph.add_node("research", research_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("review", review_node)

    graph.add_edge(START, "decompose")
    graph.add_conditional_edges("decompose", plan_research, ["research"])
    graph.add_edge("research", "synthesize")
    graph.add_edge("synthesize", "review")
    graph.add_conditional_edges("review", needs_revision, {END: END, "synthesize": "synthesize"})

    return graph


def build_capstone_app(checkpointer: Any) -> Any:
    """Compile the capstone with a checkpointer.

    The checkpointer is **required** here — `interrupt()` doesn't work
    without one. Pass `SqliteSaver` for single-machine / portfolio use,
    `AsyncPostgresSaver` for production multi-process.
    """
    if checkpointer is None:
        raise ValueError(
            "build_capstone_app requires a checkpointer — interrupt() needs durable state. "
            "Pass SqliteSaver(...) for dev or AsyncPostgresSaver(...) for production."
        )
    return build_capstone_graph().compile(checkpointer=checkpointer)


def empty_state(topic: str) -> CapstoneState:
    """Helper: well-typed empty state to seed the first invocation."""
    return CapstoneState(
        topic=topic,
        sub_questions=[],
        findings=[],
        draft=None,
        approved=False,
        reviewer_notes=None,
        revision_count=0,
    )
