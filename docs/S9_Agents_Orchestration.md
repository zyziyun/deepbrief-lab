# S9 — Agents: Orchestration

> *"S8 taught you to build one agent. S9 teaches you when to stop at one — and what changes the moment you can't."*

S7 gave you **MCP** (how agents reach tools) and **A2A** (how agents talk to each other). S8 gave you the **single-agent loop** and **bounded autonomy**. S9 is the layer above both: **when to compose multiple agents, what runtime to run them on, and what production primitives you need that you couldn't easily build by hand.**

This session deliberately does **not** re-explain:
- the ReAct loop (S8 §3)
- MCP transports / lifecycle (S7 §3-§4)
- A2A AgentCard discovery (S7 §7)
- the HITL 4-tier taxonomy (S8 §5.5)

Cross-reference them; we'll only mention them where their interface changes.

The hands-on companion is **deepbrief-lab** — `notebook 07b` (LangGraph rewrite), `notebook 08` (capstone extension with `SqliteSaver` + `interrupt()`), and `notebook 09` (Redis Streams worker pipeline). Tool-RAG hands-on lives in the **voyageai-python-service** repo where the production implementation already exists.

---

## Part 1 — When (and When Not) to Reach for Multiple Agents

### 1.1 The Three Forces

Multi-agent systems exist for three engineering reasons. Knowing which force is dominant for your task tells you whether to reach for them at all.

| Force | What it pressures | When it dominates |
|---|---|---|
| **Context-window pressure** | One agent's history gets too long; later turns degrade ("context rot") | Long research tasks, multi-document synthesis |
| **Specialization pressure** | One model can't be best-in-class at coding + legal + creative + retrieval | Cross-domain tasks (research + analysis + generation) |
| **Parallelism pressure** | Sequential steps are slow; some sub-tasks are independent | Breadth-first searches, fan-out queries |

The canonical engineering case study is **Anthropic's multi-agent research system writeup**. Two sentences worth memorizing:

> *"Subagents facilitate compression by operating in parallel with their own context windows, exploring different aspects of the question simultaneously before condensing the most important tokens for the lead research agent."*
>
> *"Multi-agent architectures effectively scale token usage for tasks that exceed the limits of single agents."*

### 1.2 The Cost Numbers (every interviewer knows these)

These come from Anthropic's published engineering data. Reference them by memory in interviews.

| Metric | Value | Source |
|---|---|---|
| Multi-agent vs. plain chat token usage | **~15×** | Anthropic |
| Single agent (with tools) vs. plain chat | **~4×** | Anthropic |
| Multi-agent vs. single agent (derived) | **~3.75×** | 15 ÷ 4 |
| Anthropic's multi-agent vs. single Opus on research benchmark | **+90.2%** | Anthropic |
| Performance variance explained by token usage | **~80%** | Anthropic |

**One-paragraph interview answer to "is multi-agent worth it?"**

> *"Anthropic published that multi-agent systems use roughly 15× the tokens of standard chat and 3-4× the tokens of a single agent with tools, with a 90.2% lift on research benchmarks. The honest read: multi-agent works when task value justifies the token cost AND the task naturally decomposes into independent parallel sub-searches. For most production workloads it's overkill. The interesting question is which of my product's queries actually need it — usually a small fraction."*

### 1.3 The Counter-Argument

The 2026 Stanford finding cuts the other direction:

> *"Single-agent systems match or outperform multi-agent architectures on complex reasoning tasks when both are given the same compute budget."*

**Mechanism:** a single agent has the full context, makes coherent decisions, and doesn't lose information across handoffs. Multi-agent systems pay a **coordination tax** — every handoff drops information. Anthropic's own data agrees on tightly-coupled tasks: single agent wins on most coding work.

**Synthesis:** multi-agent wins when **(a)** sub-tasks are genuinely independent and parallelizable AND **(b)** each subagent has its own context window's worth of useful work. It loses on tightly-coupled tasks.

### 1.4 The Three Dominant Patterns

These three cover ~90% of multi-agent systems in production. The names matter — interviewers will use them.

#### Pattern A — Planner / Executor

```
user goal
   │
   ▼
┌─────────┐  plan: [step1, step2, step3]  ┌──────────┐
│ Planner │ ────────────────────────────▶ │ Executor │
└─────────┘                               └──────────┘
                                                │
                                                ▼
                                          final output
```

| When it wins | When it fails |
|---|---|
| Goal can be planned up-front | Plan goes stale after step 1 (use ReAct instead) |
| Sub-steps mostly independent or strictly sequential | Executor hits errors planner didn't anticipate |
| Replanning is rare | Planner over-decomposes into 20 micro-steps |

A useful production note: **most planner-executor systems in the wild are actually *iterative replanning*** — planner runs, executor reports back, planner revises. That's closer to ReAct in cost. Pure single-shot plan-then-execute is rare.

#### Pattern B — Supervisor / Specialists

```
user query
   │
   ▼
┌────────────┐ routes  ┌─────────────┐ (research)
│ Supervisor │────────▶│ Specialist A│
│            │────────▶│ Specialist B│ (legal)
│            │────────▶│ Specialist C│ (writer)
└────────────┘         └─────────────┘
      │
      │ synthesizes
      ▼
 final answer
```

This is **Anthropic's research system shape** and the most common production pattern. A useful empirical note: Anthropic's reported 90.2% lift came from **Opus as supervisor + Sonnet as subagents** — heavier model on the routing/synthesis, cheaper model on the parallel work. **The cost-quality tradeoff isn't symmetric.**

This is also the shape that `deepbrief-lab` ships out of the box: `Coordinator` decomposes a topic, three `Researcher` agents do parallel sub-searches via A2A, then synthesis.

| When it wins | When it fails |
|---|---|
| Cross-domain query (research + analysis + writing) | All "specialists" are basically doing the same thing — pure cost increase, zero specialization gain |
| Sub-agents can run in parallel | Supervisor itself becomes the context bottleneck (it sees all results) |
| Each specialist has a meaningfully different prompt or tool set | Routing fails — supervisor sends queries to wrong specialist |

#### Pattern C — Verification Swarm

```
coder agent
   │
   ▼
artifact ──▶ security critic  ──┐
        │                       ├──▶ revise OR approve
        └──▶ architecture critic ┘
```

The trick that makes this pattern work or fail: **orthogonality**. If all three critics use the same model and the same prompt style, you have correlated blind spots — three GPT-4o critics agreeing on a GPT-4o draft is mostly noise. Use a **different model family** or critics whose prompts emphasize **different failure modes** (security vs. correctness vs. style).

| When it wins | When it fails |
|---|---|
| High-stakes outputs (security-critical, regulated) | All critics same model + same style = correlated failure |
| Failure dimensions are orthogonal | Critics disagree without resolution → infinite revision loop |
| You want defense-in-depth at the model layer | Critic cost matches the original generation — doubled spend, marginal lift |

### 1.5 Three Anti-Patterns

These four are common enough to be worth naming explicitly. Avoid them in interviews and in code.

| Anti-pattern | Why it fails |
|---|---|
| **Echo chamber agents** | Same model + same prompt + 3 instances = correlated outputs, no value added |
| **Cascading hallucination** | Each agent confidently builds on previous wrong answer (no grounding step) |
| **The committee problem** | 5+ agents involved in every decision; 80% of latency is coordination |
| **Over-decomposition** | Task split into 15 micro-steps where inter-step communication exceeds the work |

---

## Part 2 — LangGraph Mental Model

The 6-line agent loop from S8 is fine for a single agent with tools.

**The moment you need branching, cycles, parallel sub-agents, durable HITL, or cross-restart recovery, you need a runtime that handles state explicitly.**

**LangGraph is that runtime.**

### 2.1 Architecture in 2026

**LangChain 1.0 (released October 2025) is built on top of LangGraph.**

When you call `create_agent()` in modern LangChain, you're using LangGraph internally. They're not competing frameworks — they're different abstraction levels of the same system.

| Layer | What it provides | When to reach for it |
|---|---|---|
| **LangChain** | High-level `create_agent()`, prebuilt patterns, middleware | Common patterns, tool calling, simple RAG |
| **LangGraph** | Low-level `StateGraph`, nodes, edges, reducers, checkpointers | State machines, cycles, parallel branches, durable HITL, custom control flow |

> *"LangChain is the developer-experience layer, LangGraph is the orchestration runtime. In 2026 they ship together. I drop to LangGraph directly when I need state machines, parallel fan-out, durable interrupts, or time-travel debugging."*

### 2.2 Mental Model: a Graph Whose Edges Carry State

The shift from chains to graphs is a real cognitive jump. Hold these four ideas:

1. **Nodes are pure functions** of the form `(state) -> partial_state_update`. Not classes, not coroutines — just functions that read state and return a dict of updates.
2. **Edges carry control flow**, not data. Data moves via the shared state object.
3. **State updates are merged by reducers.** This is not assignment — every field declares how its updates combine.
4. **Execution is organized into supersteps** (borrowed from Pregel). Inside a superstep, all eligible nodes run in parallel; barriers between supersteps wait for all to complete.

The canonical state declaration:

```python
from typing import Annotated, TypedDict, Sequence
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END, add_messages
import operator

class AgentState(TypedDict):
    messages:     Annotated[Sequence[BaseMessage], add_messages]   # reducer: append
    findings:     Annotated[list[dict], operator.add]              # reducer: list-extend
    cost_usd:     Annotated[float, operator.add]                   # reducer: numeric-sum
    plan:         list[str]                                         # no reducer: replace
    current_step: int                                               # no reducer: replace
```

Five fields, four field semantics. The `TypedDict` is the schema; the `Annotated[..., reducer]` syntax declares the merge rule. **State design is the most important architectural decision in a LangGraph system** — too narrow and nodes pass data out-of-band, too wide and you have an unmanageable blob.

### 2.3 Reducers: The One Thing Beginners Get Wrong

This is the single most commonly-asked LangGraph design question:

> *"In a multi-agent LangGraph system, what happens if two parallel branches both modify the same state field that has no reducer defined?"*

**Answer: the second write silently clobbers the first.** Default reducer is overwrite. If two parallel nodes both return `{"findings": [...]}` and `findings` has no reducer, you keep only one of them — non-deterministically based on completion order.

The fix is to declare a reducer that knows how to merge:

```python
# WRONG — silent data loss in parallel branches
class State(TypedDict):
    findings: list[dict]            # default reducer: overwrite

# RIGHT — list-append semantics
class State(TypedDict):
    findings: Annotated[list[dict], operator.add]    # extend lists

# ALSO RIGHT — custom merge logic
def merge_findings(left: list[dict], right: list[dict]) -> list[dict]:
    seen = {f["id"] for f in left}
    return left + [f for f in right if f["id"] not in seen]    # dedupe by id

class State(TypedDict):
    findings: Annotated[list[dict], merge_findings]
```

| Reducer | Use case |
|---|---|
| Default (overwrite) | Single-writer fields like `current_step`, `plan` |
| `operator.add` (list extend, numeric sum) | Multi-writer accumulators: findings, results, cost |
| `add_messages` | Conversation history (handles message id dedup) |
| Custom function | Dedup, set union, structured merge |

### 2.4 Building the Graph

The structural primitives:

```python
graph = StateGraph(AgentState)

# Nodes are pure functions
graph.add_node("planner",  planner_fn)
graph.add_node("executor", executor_fn)
graph.add_node("reviewer", reviewer_fn)

# Edges define control flow
graph.add_edge(START, "planner")
graph.add_edge("planner", "executor")

# Conditional edges — branching at runtime
def needs_review(state: AgentState) -> str:
    return "reviewer" if state["risk"] == "high" else END

graph.add_conditional_edges("executor", needs_review,
                            {"reviewer": "reviewer", END: END})

# Cycles are first-class — review can loop back to executor
graph.add_edge("reviewer", "executor")

app = graph.compile()
```

Three things to notice:

1. **Cycles are explicit**, not snuck in. Interviewers will probe "can your graph have loops?" The answer is yes, and the runtime handles them via supersteps.
2. **Conditional edges are functions of state.** They run on every transition; the function returns the name of the next node. The mapping dict is required and is a common silent-bug source — typo a node name and the graph silently routes to END.
3. **`add_edge(START, "planner")` and `add_edge("...", END)`** are how entry and exit are declared. `START` and `END` are sentinel nodes from `langgraph.graph`.

### 2.5 Parallel Fan-Out: Static Edges vs. the Send API

Two distinct patterns. Both run in parallel; they differ in whether the parallelism is **fixed at graph-build time** or **decided at runtime**.

**Static fan-out** — multiple edges from one source. The graph detects the pattern and runs the destinations concurrently in a superstep:

```python
graph.add_edge("router", "search_arxiv")
graph.add_edge("router", "search_wikipedia")
graph.add_edge("router", "search_internal")
# all three run in parallel
graph.add_edge("search_arxiv",     "synthesize")
graph.add_edge("search_wikipedia", "synthesize")
graph.add_edge("search_internal",  "synthesize")
# synthesize waits for all three
```

This works when the **number of branches is fixed** at compile time.

**Dynamic fan-out via `Send`** — branches decided at runtime. This is the pattern that's easy to miss:

```python
from langgraph.types import Send

def route_to_workers(state: AgentState) -> list[Send]:
    # Number of branches depends on state, not graph structure
    return [
        Send("worker", {"query": q, "agent_id": i})
        for i, q in enumerate(state["sub_questions"])
    ]

graph.add_conditional_edges("planner", route_to_workers)
```

Each `Send(node, payload)` creates a branch with **its own payload** — different from broadcasting the full state. This is **map-reduce inside LangGraph**: the planner produces N tasks, N workers run in parallel, the aggregator merges results.

> *"Your research agent decomposes a query into a variable number of sub-questions. How do you fan out?"*
>
> **Wrong answer:** static edges (you don't know N at compile time).
> **Right answer:** `Send` API in a conditional edge — N branches dispatched dynamically.

### 2.6 Supersteps and the "All-or-Nothing" Trap

LangGraph's execution model groups parallel work into **supersteps** (the term comes from Google's Pregel paper). Inside a superstep, all selected nodes run concurrently. A barrier at the end waits for all of them before the next superstep starts.

When a node fails mid-superstep, LangGraph stores **pending writes** from successfully-completed nodes in that superstep. On resume, the successful nodes are **not re-run**; only the failed one (and downstream nodes) execute. This is a core advantage of LangGraph over hand-rolled retry loops where you'd re-run everything.

| Implication | What you do about it |
|---|---|
| One slow branch blocks the whole superstep | Set per-node timeouts; consider defer pattern for asymmetric branches |
| Failure is all-or-nothing within a superstep | Catch errors inside nodes, return error markers in state instead of raising |
| Memory grows linearly with branch count | Keep state payloads small; pass IDs, not bytes |
| Large fan-outs (50+) hit API rate limits | Batch via Send chunks + retry-with-backoff inside node |

**Follow-up question:** *"What does LangGraph guarantee on a process crash mid-superstep?"*

Answer: with a checkpointer configured, the **last completed superstep** is durable. The crashed superstep is replayed from the start; nodes that already wrote state in the previous superstep are not re-run. This is the resumability story we'll unpack in Part 3.

### 2.7 Preview: the Three Killer Primitives

Beyond the graph itself, LangGraph ships three production-grade primitives that you'd otherwise build yourself:

| Primitive | What it gives you | Replaces (in hand-rolled code) |
|---|---|---|
| **Checkpointer** | Per-superstep state persistence to Postgres/SQLite/Mongo | Manual snapshotting + Redis state store |
| **`interrupt()`** | Durable HITL — graph pauses indefinitely until human resumes | Custom approval table + webhook + state-load logic |
| **Time travel** | Inspect / fork / replay any prior checkpoint | Custom event sourcing |

Each is small in API surface and large in production value. We unpack them next.

---

## Part 3 — The Three Killer Primitives

This is the section that pays off in interviews. Each primitive answers a standard interview question, and a complete answer mentions all three.

### 3.1 Checkpointer — State That Survives Restarts

The default LangGraph behavior is **in-memory state**. `app.invoke({...})` runs the graph, returns the final state, and forgets everything. Production needs the opposite.

A **checkpointer** writes state to durable storage **after every superstep**. With a checkpointer configured, two things become possible:

1. **Multi-turn conversations** — invoke the same `thread_id` across requests; state loads automatically.
2. **Crash recovery** — process dies mid-graph; on restart with the same `thread_id`, execution resumes from the last checkpoint.

```python
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.memory import InMemorySaver

# For dev / tests:
checkpointer = InMemorySaver()

# For production:
checkpointer = PostgresSaver.from_conn_string("postgresql://...")
checkpointer.setup()                              # creates tables once

app = graph.compile(checkpointer=checkpointer)

# Every invocation requires a thread_id config
config = {"configurable": {"thread_id": "user-42-conv-1"}}
result = app.invoke({"messages": [user_msg]}, config=config)

# Hours later, on a different machine:
result = app.invoke({"messages": [next_msg]}, config=config)
# state is loaded from Postgres automatically
```

**Three things to call out in interviews:**

**(a) Thread ID is the unit of state isolation.** Each `thread_id` has its own checkpoint history. This is how multi-tenant systems share one graph across millions of conversations — the state schema is the same, the data is partitioned by `thread_id`.

**(b) Backend choice is a real design decision.** Common interview question: *"AsyncPostgresSaver vs InMemorySaver — when do you pick each?"*

| Backend | Use case | Why |
|---|---|---|
| `InMemorySaver` | Tests, single-process dev | Zero setup; lost on restart |
| `SqliteSaver` | Single-machine prod, demos | File-backed; good for portfolio projects |
| `PostgresSaver` / `AsyncPostgresSaver` | Production multi-process | Concurrent writers, transactional, scales horizontally |
| `MongoDB` checkpointer (community) | Already running on Mongo | Avoids adding Postgres just for LangGraph |

The async variants matter when your nodes are async — using sync `PostgresSaver` from async code blocks the event loop. **Match async-ness across your stack.**

**(c) Checkpointer ≠ Store.** This is a subtle distinction interviewers often probe:

| | Checkpointer | Store |
|---|---|---|
| **Scope** | Within a single `thread_id` | Across all threads |
| **Purpose** | "What happened in this conversation" | "What I know about this user / this domain" |
| **Used for** | Multi-turn state, resumability | Long-term memory, cross-session preferences |
| **Lifetime** | Per conversation | Per user / namespace |

If a candidate conflates these, the interviewer notices. The mental model: **checkpointer is short-term memory within a thread, store is long-term memory across threads.**

### 3.2 `interrupt()` — Durable Human-in-the-Loop

S8 §5.5 introduced the **4-tier HITL taxonomy** (auto / logged async / sync / block). We built sync HITL by hand with a file-backed `ApprovalStore` that polled for decisions.

That works but has problems: state is held in memory while waiting, a worker recycle loses the request, the polling pattern wastes cycles. LangGraph's `interrupt()` solves all three.

```python
from langgraph.types import interrupt, Command

def review_node(state: AgentState):
    # Pause the graph. State is checkpointed. Process can exit.
    decision = interrupt({
        "kind": "approve_action",
        "tool": state["pending_tool"],
        "args": state["pending_args"],
        "question": "Approve, edit, or reject?",
    })
    # When resume() is called, this line runs and `decision` holds the response
    return {"approval": decision}
```

When the graph hits `interrupt(...)`, four things happen:

1. **State is checkpointed** (this is why the checkpointer is required).
2. **The graph returns control to the caller** with an `__interrupt__` marker in the result.
3. **The process can exit, restart, redeploy.** State is durable.
4. **When `app.invoke(Command(resume=value), config=config)` is called later**, the graph rehydrates from the checkpoint, the `interrupt(...)` call returns `value`, and execution continues.

```python
# First call — runs until interrupt
result = app.invoke({"messages": [...]}, config=config)
# result["__interrupt__"] contains the pause payload

# ... hours/days later, after a human reviews ...

# Resume with the human decision
result = app.invoke(Command(resume="approve"), config=config)
```

**Two things to know in interviews:**

**(a) Interrupt is a function call inside a node, not a config flag.** This is different from older pause-before-tool patterns. The node decides when to interrupt, based on state. You can interrupt unconditionally for tier-2 actions, or conditionally based on risk score:

```python
def maybe_interrupt(state: AgentState):
    if state["risk_score"] > 0.8:
        decision = interrupt({...})
        return {"approval": decision}
    return {"approval": "auto"}      # tier-1: auto-approve
```

**(b) The `Command(resume=...)` payload is what `interrupt(...)` returns.** It can be anything — a string, a dict, an edited tool-call. This is how "edit the action before running" works:

```python
# Reviewer can modify the proposed action before resuming
app.invoke(
    Command(resume={"action": "approve_with_edit",
                    "edited_args": {"to": "alice@new.com"}}),
    config=config,
)
```

**Mapping back to S8's HITL 4-tier:**

| Tier | LangGraph implementation |
|---|---|
| Tier 0 (auto, log only) | No interrupt; just trace |
| Tier 1 (logged async) | No interrupt; emit event to async reviewer queue |
| Tier 2 (sync HITL) | `interrupt()` — block until human responds |
| Tier 3 (hard block) | Don't even propose; refuse in the planner |

**deepbrief-lab `notebook 08` extension** (capstone upgrade) swaps the hand-rolled `ApprovalStore` from S8 for `SqliteSaver` + `interrupt()` and shows the resume flow with the editor closing their browser between request and approval.

### 3.3 Time Travel — Inspect, Fork, Replay

Production agents are non-deterministic. Same input → different output. Two consequences:

1. **Bug reports are hard to reproduce.** *"The agent gave the wrong answer yesterday"* — what state did it see? What tool did it call? What did the tool return?
2. **Improving prompts is hard to A/B test.** *"Would the agent have done better if I'd written this prompt differently?"* — you'd need to rerun, but reruns produce different traces.

Time travel solves both. Every checkpointer entry is a snapshot of state before/after each superstep. You can list them, inspect them, fork from any of them.

```python
# List the entire state history of a thread
for snapshot in app.get_state_history(config):
    print(snapshot.values, snapshot.next, snapshot.metadata)

# Inspect one snapshot
state_at_step_3 = list(app.get_state_history(config))[3]
print(state_at_step_3.values["findings"])

# Fork: rerun from a prior checkpoint with modified state
modified_config = {
    "configurable": {
        "thread_id": "user-42-conv-1",
        "checkpoint_id": state_at_step_3.config["configurable"]["checkpoint_id"],
    }
}

# Update state at the fork point and re-invoke
app.update_state(modified_config, {"plan": ["new_plan_step_1"]})
app.invoke(None, modified_config)              # continues from fork with new state
```

This is the standard production-debugging answer for the *"user reports the agent gave a different answer this time"* interview question:

> *"I'd pull the thread's checkpoint history, find the divergence point — usually a tool call that returned different data — and either replay from that checkpoint with the same inputs to confirm reproduction, or fork at an earlier checkpoint with a fixed prompt to verify the fix. Without checkpointers I'd be stuck reading logs and guessing."*

A real worked example of this pattern: see the [DEV.to article on time-travel debugging a banking loan-rejection agent](https://dev.to). Worth reading once; the pattern generalizes far beyond banking.

### 3.4 Putting the Three Together

These three primitives compose. The complete production shape, distilled:

```python
from langgraph.checkpoint.postgres import AsyncPostgresSaver
from langgraph.types import interrupt, Command

class ResearchState(TypedDict):
    topic: str
    sub_questions: list[str]
    findings: Annotated[list[dict], operator.add]
    draft: str | None
    approved: bool

graph = StateGraph(ResearchState)
graph.add_node("decompose",  decompose_node)
graph.add_node("research",   research_node)     # uses Send for parallel fan-out
graph.add_node("synthesize", synthesize_node)
graph.add_node("review",     review_node)       # contains interrupt()

graph.add_edge(START, "decompose")
graph.add_edge("decompose", "research")
graph.add_edge("research", "synthesize")
graph.add_edge("synthesize", "review")
graph.add_conditional_edges("review",
    lambda s: "synthesize" if not s["approved"] else END)

# Wire all three primitives
checkpointer = AsyncPostgresSaver.from_conn_string("postgresql://...")
app = graph.compile(checkpointer=checkpointer)

# At call time:
config = {"configurable": {"thread_id": user_thread_id}}
result = await app.ainvoke({"topic": "X"}, config=config)
# may include __interrupt__; resume with Command(resume=...)
# at any point, app.get_state_history(config) gives the full audit log
```

**This is the four-line argument for LangGraph in production**: a state machine, durable across crashes, with HITL gates, fully auditable. Building these four pieces by hand on top of a hand-rolled agent loop is months of work.

This is exactly what **deepbrief-lab `notebook 07b`** rewrites the original `Coordinator` into. Same inputs (topic), same output (`brief.md`), same sub-question count — different runtime. The diff is what `notebook 07b` is designed to make visible.

---

## Part 4 — Tool-RAG: Semantic Tool Selection at Scale

What do you do with 30, 50, 200 tools?

### 4.1 The Problem

Tool descriptions go into the LLM context on **every request**. Per-tool overhead is roughly 200 tokens (name + description + parameter schema). The math:

| Tool count | Token tax / request | What breaks |
|---|---|---|
| 5 tools | ~1k | Trivial; ignore |
| 20 tools | ~4k | Noticeable; consider grouping |
| 50 tools | ~10k | Burning tokens on tool defs every turn; selection accuracy starts dropping |
| 100+ | ~20k+ | Selection accuracy collapses; latency balloons |

Two failures happen together at scale:

- **Cost** — paying for tool definitions on every turn
- **Selection accuracy** — too many tools confuses the LLM

The fix is **Tool-RAG**: index the tool catalog by embedding, retrieve only the top-k relevant tools per query, send those to the LLM. This is RAG, but the "documents" are tool descriptions.

### 4.2 The Pattern

```
Index time (once):
  for each tool in catalog:
      vector = embed(tool.name + tool.description + examples)
      vector_db.upsert(tool.id, vector, metadata={tool_def})

Query time (per request):
  query_vector = embed(user_query)
  top_k = vector_db.query(query_vector, k=8)
  tools_for_this_request = [t.metadata for t in top_k] + ALWAYS_INCLUDE
  llm.complete(messages, tools=tools_for_this_request)
```

The complete pattern adds two things that aren't obvious from a 5-line description: a **skip threshold** and an **always-include core**.

### 4.3 The Skip Threshold

Tool-RAG isn't free. Each query now requires:

- One extra embedding call (~5ms, ~$0.00001)
- A vector DB lookup (~10ms)
- A more complex code path (more failure modes)

**If you have fewer than 20 tools, just send all of them.** The skip threshold is empirical:

| Tool count | Strategy |
|---|---|
| < 20 | Send all tools directly. Tool-RAG overhead exceeds the token saving. |
| 20-50 | Tool-RAG with top-k = 8-12 |
| 50-100 | Tool-RAG with top-k = 5-8 + always-include core |
| 100+ | Tool-RAG + hierarchical grouping (categories first, then tools within category) |

> *"How do you handle 25 tools in your agent?"*
>
> **Wrong answer:** "Tool-RAG."
> **Right answer:** *"It depends. At 25 I'd benchmark sending them all with strict mode against Tool-RAG with top-k=10 on a real eval set. Until I can show Tool-RAG actually wins on quality plus cost, I'd default to sending them all because it's simpler and avoids retrieval as an attack surface."*

### 4.4 Always-Include Core Tools

The pattern that production teams converge on:

```python
selected = list(CORE_TOOL_NAMES) + retrieve_top_k(query, all_tools - CORE_TOOL_NAMES)
selected = list(set(selected))    # dedupe
```

Why "always-include"?

- Some tools are needed on **almost every query** — geocoding before weather lookup, web search for fact-checks, current-date for any time-sensitive query.
- Tool-RAG might miss these on novel phrasings; one bad retrieval ruins the run.
- The token cost is fixed and small.

Heuristic for choosing core tools:

| Pick as core if | Example |
|---|---|
| Used on > 40% of historical queries | `web_search` in a research agent |
| Prerequisite for other tools | `geocode_city` before `get_weather` |
| High coverage and low specificity | generic `fetch_url`, `current_datetime` |

### 4.5 Implementation Sketch

The minimal production implementation. Uses OpenAI `text-embedding-3-small` and Chroma; both swap-able.

```python
from openai import AsyncOpenAI
import chromadb

CORE_TOOL_NAMES = {"geocode_city", "get_weather"}

class ToolIndex:
    def __init__(self, registry, embed_model: str = "text-embedding-3-small"):
        self.registry = registry
        self.embed_client = AsyncOpenAI()
        self.embed_model = embed_model
        self.collection = chromadb.Client().create_collection(
            name="tools", metadata={"hnsw:space": "cosine"},
        )

    async def build(self) -> None:
        non_core = [t for t in self.registry.all() if t.name not in CORE_TOOL_NAMES]
        if not non_core:
            return
        texts = [f"{t.name}: {t.description}" for t in non_core]
        resp = await self.embed_client.embeddings.create(input=texts, model=self.embed_model)
        self.collection.add(
            ids=[t.name for t in non_core],
            embeddings=[e.embedding for e in resp.data],
            documents=texts,
        )

    async def select(self, query: str, top_k: int = 8) -> list[dict]:
        all_tools = list(self.registry.all())
        if len(all_tools) < 20:
            return [t.to_openai_schema() for t in all_tools]      # skip threshold

        q_emb = (await self.embed_client.embeddings.create(
            input=[query], model=self.embed_model
        )).data[0].embedding

        retrieved = self.collection.query(query_embeddings=[q_emb], n_results=top_k)
        names = set(retrieved["ids"][0]) | CORE_TOOL_NAMES
        return [self.registry.get(n).to_openai_schema() for n in names]
```

Three things worth pointing out:

**(a) Tool-RAG runs once per run, not per turn.** The selected subset is fixed for the duration of the agent loop. Per-turn re-retrieval is a more advanced pattern that adds latency; start with per-run.

**(b) Use `set()` to dedupe.** Core tools may also appear in retrieval results.

**(c) Empty-result fallback.** If retrieval fails (cache cold, embed API down), fall back to sending all tools. Never crash the agent on a Tool-RAG failure — degrade gracefully.

> **In the VoyageAI repo** — this exact pattern is already implemented in `src/voyageai/rag/tool_rag.py` (the `ToolRAG` singleton) with `CORE_TOOLS` and `TOOL_RAG_SKIP_THRESHOLD` constants in `src/voyageai/services/agent_types.py`. Seed metadata with example queries lives at `src/voyageai/rag/seed_definitions.py`. The companion document `docs/s9_tool_rag_walkthrough.md` walks the production code section-by-section against this lecture.

### 4.6 Failure Modes Worth Knowing

Five failure modes worth knowing by name. Several appear in arxiv 2025-2026 literature; bring them up unprompted in interviews to show you've read the field.

| Failure mode | What it looks like | Mitigation |
|---|---|---|
| **Embedding drift** | Retrieval gets worse over months as queries evolve | Re-embed quarterly; monitor recall on a fixed eval set |
| **Adversarial tool flooding** (`ToolFlood`) | Attacker registers tools whose embeddings semantically span many queries — dominates top-k for everything | Tool-registration ACL; embedding-space anomaly detection |
| **Core-tool starvation** | Top-k=8 returns 8 unrelated tools; core tools dropped | Always-include pattern (§4.4) |
| **Cold-start latency** | First query waits for index build | Build index on startup, warm on deploy |
| **Description-vs-query gap** | Tool descriptions written for engineers; queries colloquial | Rewrite descriptions in user language; add example queries |

The published material to reference:

- **Toolshed paper (arXiv 2410.14594)** — canonical RAG-Tool Fusion paper; introduces "Tool Knowledge Base" terminology.
- **Online-Optimized RAG for Tool Use (arXiv 2509.20415)** — 2025 paper on continuously updating tool embeddings from interaction feedback.
- **Graph RAG-Tool Fusion (arXiv 2502.07223)** — handles inter-tool dependencies via knowledge graph.
- **ToolFlood (arXiv 2603.13950)** — adversarial attack on tool retrieval. The one to mention unprompted.

---

## Part 5 — Worker Pipeline Orchestration

In production, agents run as workers consuming from a queue, with idempotency, durable progress emission, and acknowledgment semantics.

### 5.1 The Production Pipeline Shape

```
┌────────────────┐
│   Producer     │ POST /agent-runs → enqueue task
│   (FastAPI)    │
└───────┬────────┘
        │
        ▼
┌────────────────┐
│     Queue      │ Kafka topic / Redis Streams / SQS
│   (durable)    │
└───────┬────────┘
        │
        ▼
┌──────────────────────────────────────────┐
│ Worker                                    │
│  1. consume task                          │
│  2. idempotency lock (Redis SETNX)        │ ◀── prevents double-execution
│  3. run agent / graph                     │
│  4. emit progress events (SSE / Pub-Sub)  │ ◀── real-time UI updates
│  5. store final result (MongoDB)          │
│  6. ack queue message                     │ ◀── only after store
└──────────────────────────────────────────┘
```

Properties this gives you:

- **Crash safety** — worker dies mid-run, task is re-delivered, idempotency lock prevents duplicate execution
- **Horizontal scale** — N workers consuming the same queue
- **Real-time UX** — progress streams to UI via SSE
- **Decoupling** — producer doesn't wait; user can close browser, agent keeps running

### 5.2 Idempotency Lock with Redis SETNX

Queues at-least-once-deliver. If a worker crashes after running the agent but before acking, the task gets redelivered. Without idempotency, you run the agent twice — pay 2× tokens, possibly double-write side effects.

The pattern: **SETNX** (SET if Not eXists) — atomic claim semantics in Redis.

```python
from redis.asyncio import Redis

class IdempotencyLock:
    def __init__(self, redis: Redis, ttl_seconds: int = 600):
        self.redis = redis
        self.ttl = ttl_seconds                          # longer than max agent run

    async def acquire(self, task_id: str, worker_id: str) -> bool:
        """Returns True if this worker won the lock."""
        key = f"agent:lock:{task_id}"
        # SET key worker_id NX EX ttl — atomic claim
        return await self.redis.set(key, worker_id, nx=True, ex=self.ttl) is True

    async def release(self, task_id: str, worker_id: str) -> None:
        """Only release if WE still own it."""
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        else
            return 0
        end
        """
        await self.redis.eval(script, 1, f"agent:lock:{task_id}", worker_id)
```

The subtle part: the `release()` uses a Lua script for **atomic check-and-delete**. Without this, if the lock TTL expires between your GET and DEL, you might delete a *different* worker's lock. This is the canonical Redis distributed-lock pattern; **mention the Lua script in interviews to show you've actually shipped this.**

### 5.3 Progress Emission

Long-running agents (10-60s) need progress signals so the UI doesn't look frozen. Two common patterns: Redis Pub/Sub (lightweight, ephemeral) or Kafka topic (durable, replayable).

```python
from redis.asyncio import Redis
import json, time

class ProgressEmitter:
    def __init__(self, redis: Redis, task_id: str):
        self.redis = redis
        self.channel = f"agent:progress:{task_id}"

    async def emit(self, step: int, kind: str, payload: dict) -> None:
        event = {
            "step": step,
            "kind": kind,                    # "thought" | "tool_call" | "tool_result" | "final"
            "ts": time.time(),
            **payload,
        }
        await self.redis.publish(self.channel, json.dumps(event))
```

A FastAPI endpoint subscribes to the same channel and streams to the browser via SSE:

```python
@app.get("/runs/{task_id}/stream")
async def stream_progress(task_id: str):
    async def event_stream():
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"agent:progress:{task_id}")
        async for msg in pubsub.listen():
            if msg["type"] == "message":
                yield f"data: {msg['data'].decode()}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

**If you're on LangGraph, you get this for free with `astream_events()`** — every node emission is a typed event. This is the bridge: hand-rolled pipelines need `ProgressEmitter`; LangGraph apps expose the stream natively.

### 5.4 The Full Worker Loop

The complete shape, with correct ordering. Read carefully — **the order of these six steps matters**, and the wrong order shows up in interviews as "and then they stop talking and stare at you."

```python
async def worker_main():
    redis = await Redis.from_url(REDIS_URL)
    mongo = AsyncIOMotorClient(MONGO_URL).agents
    lock = IdempotencyLock(redis)

    while True:
        # 1. Consume from queue
        task = await consume_from_queue()        # Kafka / Redis Streams / SQS
        task_id = task["task_id"]

        # 2. Check existing result (short-circuit completed work)
        existing = await mongo.runs.find_one({"task_id": task_id})
        if existing:
            await ack(task)                       # already done, just ack
            continue

        # 3. Acquire idempotency lock (skip if someone else got it)
        if not await lock.acquire(task_id, WORKER_ID):
            await ack(task)                       # someone else is on it
            continue

        try:
            # 4. Run the agent / graph
            emitter = ProgressEmitter(redis, task_id)
            result = await app.ainvoke(
                {"query": task["query"]},
                config={"configurable": {"thread_id": task_id}},
            )

            # 5. Persist final result
            await mongo.runs.insert_one({
                "task_id": task_id,
                "query": task["query"],
                "answer": result["answer"],
                "completed_at": datetime.utcnow(),
            })

            # 6. Ack ONLY after successful persist
            await ack(task)
        except Exception:
            log.exception("agent run failed for %s", task_id)
            # Don't ack — message redelivers; idempotency lock prevents replay within TTL
        finally:
            await lock.release(task_id, WORKER_ID)
```

### 5.5 The Subtle Ordering

Three common interview questions on this:

> *"Why ack after store, not before?"*

If you ack first then crash before storing, the message is gone but no result exists — silent data loss. Always: **do the work, persist, then ack.**

> *"Why release the lock after ack?"*

If you release first then crash before acking, the message redelivers to a different worker, which now owns the lock and re-runs. By releasing last, the lock outlives the message; on redelivery, the new worker either finds the existing result (the existing-result check at step 2) or waits for TTL.

> *"What guarantees idempotency end-to-end?"*

Two layers:

- **In-flight protection** — Redis SETNX lock prevents two workers running the same task simultaneously
- **Completed-task protection** — MongoDB existing-result check short-circuits replays of finished work

Both are needed. Drop either and you get either double-execution or wasted retry work.

### 5.6 LangGraph's Role in This Picture

If your agent is a LangGraph app, several pieces simplify:

| Concern | Hand-rolled (S8) | With LangGraph |
|---|---|---|
| Idempotency lock | Manual SETNX (above) | Still needed at queue layer |
| Progress emission | Manual `emit()` per step | `app.astream_events()` gives typed events |
| State persistence within a run | None — all in memory | Postgres/Mongo checkpointer (free) |
| Crash recovery mid-run | Restart from scratch | Resume from last checkpoint via `thread_id` |
| HITL gate | Custom approval table | `interrupt()` primitive |

**Important: LangGraph's checkpointer subsumes within-run idempotency for the agent loop, but you still need queue-level idempotency for the outer message-delivery layer.** Don't conflate them.

- The checkpointer says: "if my graph crashes mid-superstep, resume from the last good checkpoint."
- The Redis lock says: "if my queue redelivers this message, don't run the graph twice."

**This is what `deepbrief-lab notebook 09` builds** — a FastAPI producer, Redis Streams queue, worker that runs the LangGraph app from `notebook 07b`, idempotency lock with SETNX, progress emission via Pub/Sub, MongoDB result store. The same shape used in `voyageai-python-service/src/voyageai/kafka/worker.py` but with Redis Streams instead of Kafka for the educational version.

---

## Part 6 — When to Skip Orchestration Entirely

Three scenarios where multi-agent and orchestration are the wrong choice. Any one is enough to argue against orchestration in interviews.

### 6.1 The Task Is a Pipeline

**Symptom:** the same exact steps every time. You're calling them "agents" because they have LLM calls inside them.

**Example:** "Summarize each chapter, then compose a digest."

**Right answer:** a deterministic Python function with N LLM calls.

```python
async def book_digest(chapters: list[str]) -> str:
    summaries = await asyncio.gather(*[summarize(c) for c in chapters])
    return await compose_digest(summaries)
```

No `StateGraph`. No supervisor. No retry-as-graph-edge. The control flow is fully determined by the input shape.

### 6.2 One Strong Single Agent Beats Three Mid Ones

**Symptom:** "we have a planner, an executor, and a reviewer" — but they're all the same model with slightly different prompts.

Why this fails:

- Same model = correlated failure modes (the verification swarm anti-pattern from Part 1)
- 3× the tokens
- 3× the latency
- More handoff friction

**Right answer:** a single strong agent (Claude Sonnet 4.6 or GPT-5.4) with strict mode + good system prompt + cost cap. Anthropic's own data agrees — single Opus 4 with good context engineering matches multi-agent on tightly-coupled tasks.

### 6.3 Compliance / Regulated Domain

**Symptom:** healthcare, finance, legal. Auditors need to know exactly what happened.

Why agents fail here:

- Non-deterministic
- Hard to explain "why did the agent do X?"
- Tool-selection variance is a compliance liability

**Right answer:** a deterministic workflow with one or two **clearly-bounded** LLM steps. Bring an LLM in for the parts that need flexibility ("summarize this clinical note"); do everything else explicitly.

### 6.4 The Decision Heuristic

> *"When do you avoid multi-agent entirely?"*
>
> **Three checks before adding orchestration:**
>
> 1. Does the task path vary meaningfully by input? If no, **it's a pipeline.**
> 2. Do you genuinely need multiple agents with different specialties? If no, **single agent.**
> 3. Does input volume × token cost justify the 4-15× multiplier? If no, **single agent.**
>
> **All three "yes" → orchestrate. Any "no" → simpler.**

---

## S9 — 90-Second Whiteboard Cheatsheet

11 takeaways · scan before any orchestration interview question.

### Section 1 — Decision: should I orchestrate at all, and how?

**1. When multi-agent**
- breadth-first decomposition
- parallelizable sub-tasks
- each sub-agent has its own useful context window
- Cost bar: ~15× tokens vs chat, ~4× single agent w/ tools — Anthropic engineering data

**2. Patterns**
- **A. Planner → Executor** — predictable plans, low novelty
- **B. Supervisor → Specialists** — cross-domain · parallel fan-out
- **C. Verification Swarm** — orthogonal critics · **NOT echo chamber**

**3. Framework**
- LangChain 1.0 wraps LangGraph — same runtime, two layers
- Default to LangChain for prototypes
- Drop to LangGraph when you need: state machines · cycles · parallel fan-out · durable interrupts · time-travel debugging

### Section 2 — LangGraph mechanics: state, parallelism, persistence, HITL, debugging

**4. State**
- `TypedDict` + reducers
- `Annotated[list, operator.add]`
- Default = overwrite → silent data loss

**5. Parallel**
- Static edges = compile-time count
- `Send` API = runtime fan-out by state
- supersteps wait for all branches

**6. Checkpointer**
- Within-thread state, durable
- Scoped by `thread_id`
- Postgres in prod · in-memory in dev

**7. Interrupt**
- Durable HITL inside a node
- Checkpointer required
- Resume: `Command(resume=...)`

**8. Time travel**
- `get_state_history(thread_id)` → inspect or fork any prior checkpoint
- The standard answer for: *"user reports the agent gave a different answer this time"* → reproduce from snapshot

### Section 3 — Tool-RAG & pipeline: scaling tool catalogs · wiring into production workers

**9. Tool-RAG**
1. Embed tool name + description into vector DB
2. Per query: embed query → top-k retrieval
3. Pass only top-k tools to the LLM
- **Skip if < 20 tools · always-include core tools**
- Watch for: ToolFlood semantic-cover attacks · embedding drift

**10. Worker Pipeline**
- Ordering matters: `consume → existing-result check → SETNX lock → run → emit progress → store → ack → release`
- Wrong order → silent data loss OR double-billing
- Release lock uses Lua script (atomic check-and-delete)

### Section 4 — When NOT to orchestrate · the discipline that earns the higher-rate interview answer

**11. When NOT to orchestrate**

| Pipeline-shaped task | Single strong agent suffices | Compliance / regulated |
|---|---|---|
| Same exact steps every time → deterministic function | 3 agents same model + prompt = echo chamber, 3× cost, 3× latency → one good agent + strict + cap | Auditors need determinism → bounded LLM steps in workflow · healthcare · finance · legal |

**Default is "don't orchestrate."**
All three checks "yes" → orchestrate. Any "no" → simpler.
Pair this cheatsheet with the lab session — **deepbrief-lab** covers S7+S8+S9 end-to-end.

---

## Interview Questions

**Reminder:** these are bare questions; answers are covered in lecture content above. Tier and role tags help students prioritize.

⭐ = **High-frequency essential** — almost guaranteed in the relevant role's loop.
✓ = **Commonly tested** — expect on most loops.

### Junior (J) — fundamentals

1. (J) ⭐ AI Engineer — What is multi-agent architecture? When is it needed? Give one concrete example.
2. (J) ⭐ AI Engineer — What is a planner agent vs. an executor agent? How is this different from a single ReAct agent?
3. (J) ⭐ AI Engineer — What is Tool-RAG? Why would you need it?
4. (J) ⭐ AI Engineer — What is a state machine in the context of an agent? How does it differ from a chain?
5. (J) ⭐ AI Engineer — Name three multi-agent orchestration patterns. Give one example use case for each.
6. (J) ⭐ AI Engineer — In LangGraph, what is a checkpointer? What problem does it solve?

### Mid-level (M) — implementation depth

1. (M) ⭐ AI Engineer — **LangGraph vs LangChain — what is the architectural difference in 2026?** When do you reach for which?
2. (M) ⭐ AI Engineer — How do you handle failures and retries in multi-agent systems? Walk through the layers (queue, idempotency, result existence).
3. (M) ⭐ AI Engineer — How do you decompose a complex task across multiple specialized agents? What are the tradeoffs of finer-grained vs. coarser-grained decomposition?
4. (M) ✓ AI Engineer — What is a verification swarm (e.g., coder → security agent → architecture agent)? When does it work and when does it fail?
5. (M) ⭐ AI Engineer — What is the **always-include core tools** pattern in Tool-RAG? Why is it needed even with good retrieval?
6. (M) ⭐ AI Engineer ✓ MLE — Implement a Redis-based **idempotency lock** for an agent worker. Why must release use a Lua script and not just DEL?
7. (M) ⭐ AI Engineer — In LangGraph, what is `interrupt()`? Why is it called "durable HITL," and how is it different from a regular blocking call to a human reviewer?
8. (M) ⭐ AI Engineer — In a worker pipeline (`consume → lock → run → emit → store → ack`), why is the order important? What goes wrong if you ack before storing?
9. (M) ⭐ AI Engineer — In Tool-RAG, what is the **skip threshold** and why does it exist? At what tool count would you cross the threshold and what changes operationally?
10. (M) ⭐ AI Engineer — **In a LangGraph multi-agent system, what happens if two parallel branches both modify the same state field that has no reducer defined?** How would you fix it?
11. (M) ⭐ AI Engineer — In LangGraph, what is the difference between **static fan-out** (multiple edges from one node) and **dynamic fan-out via `Send`**? When does each apply?

### Senior (S) — design and judgment

1. (S) ⭐ AI Engineer — **Single-agent vs multi-agent: concrete tradeoffs in cost, latency, reliability.** Use Anthropic's published numbers. When does multi-agent earn its 15× cost? When does it not?
2. (S) ⭐ AI Engineer — Multi-agent token consumption can be 4-15× single-agent. **How do you control it in production?** Give 4 distinct levers.
3. (S) ⭐ AI Engineer — **Design a multi-agent orchestration system** with shared state and checkpointing. Cover: state schema design, reducer choices, checkpointer backend, `thread_id` strategy, recovery semantics.
4. (S) ⭐ AI Engineer — You have a complex agentic system with 5 specialized agents vs. a simpler single-agent with 25 tools. **How do you decide which to ship?** What experiment do you run?
5. (S) ⭐ AI Engineer — **When should you avoid multi-agent systems entirely** and use a deterministic pipeline? Give three concrete scenarios.
6. (S) ⭐ AI Engineer — **Design a Tool-RAG system** for an agent with 100 tools. Cover: indexing pipeline, embedding model choice, top-k strategy, always-include core, failure modes (cold start, embedding drift, adversarial flooding).
7. (S) ⭐ AI Engineer — **Adversarial tool flooding (ToolFlood, 2026):** an attacker registers tools whose embeddings semantically span many queries, dominating top-k retrieval. How do you defend?
8. (S) ⭐ AI Engineer — **A worker crashes mid-run.** Walk through what happens at the queue, lock, and storage layer. What guarantees idempotency? What if the lock TTL expires before recovery?
9. (S) ⭐ AI Engineer — In LangGraph, `interrupt()` pauses the graph for human approval. Walk through what's checkpointed, what state is preserved, what happens if the worker dies, and how the response is fed back via `Command(resume=...)`.
10. (S) ⭐ AI Engineer — Anthropic reports multi-agent uses 15× tokens but delivers +90.2% on research. When is this tradeoff economically defensible? Give a back-of-envelope cost calculation.
11. (S) ⭐ AI Engineer — **Verification swarm anti-pattern: 3 GPT-4o critics on a GPT-4o draft.** Why does this add little value? How would you redesign for orthogonal failure modes?
12. (S) ⭐ AI Engineer — **Design the orchestration control plane** for a company running 20+ agents. What's shared infrastructure (queue, lock service, checkpointer DB, progress bus, audit log)? What's the failure-isolation story?
13. (S) ⭐ AI Engineer — **A user reports the agent gave a different answer this time for the same query.** Walk me through how you'd reproduce, where in the LangGraph trace you'd look (hint: `get_state_history`), and what fixes you'd consider.
14. (S) ⭐ AI Engineer — **Your team wants to migrate from a hand-rolled agent loop to LangGraph** for orchestration. What's your migration plan? What do you keep, what do you replace, and what new failure modes does LangGraph introduce?
15. (S) ⭐ AI Engineer — In LangGraph, what is the difference between `Store` (cross-thread memory) and `Checkpointer` (within-thread state)? Give a use case for each.
16. (S) ⭐ AI Engineer — **Supersteps are atomic for state writes.** Why does this matter? What goes wrong if one branch in a parallel fan-out fails?
17. (S) ⭐ AI Engineer — `AsyncPostgresSaver` vs `InMemorySaver` — when do you pick each? What goes wrong if you use sync `PostgresSaver` from async nodes?

---

## Appendix — Verified Reference Links

### LangGraph

- [LangGraph official docs](https://langchain-ai.github.io/langgraph/)
- [LangGraph reducers reference](https://langchain-ai.github.io/langgraph/concepts/low_level/#reducers)
- [LangGraph persistence](https://langchain-ai.github.io/langgraph/concepts/persistence/)
- [LangGraph human-in-the-loop](https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/)
- [LangChain interrupt blog](https://blog.langchain.dev/making-graphs-resumable-with-interrupts/)
- [LangChain 1.0 vs LangGraph 1.0](https://blog.langchain.dev/langchain-1-0/)
- [LangGraph Send / map-reduce](https://langchain-ai.github.io/langgraph/how-tos/map-reduce/)

### Multi-agent engineering

- [Anthropic — multi-agent research system writeup](https://www.anthropic.com/news/multi-agent-research-system)
- [Stanford 2026 single-agent vs multi-agent finding (summary on ampcome.com)](https://ampcome.com)
- [Inductivee — multi-agent LangGraph deep dive](https://inductivee.com)

### Tool-RAG

- [Toolshed paper — arXiv 2410.14594](https://arxiv.org/abs/2410.14594)
- [Online-Optimized RAG for Tool Use — arXiv 2509.20415](https://arxiv.org/abs/2509.20415)
- [Graph RAG-Tool Fusion — arXiv 2502.07223](https://arxiv.org/abs/2502.07223)
- [ToolFlood — arXiv 2603.13950](https://arxiv.org/abs/2603.13950)
- [Red Hat — Tool RAG: The Next Breakthrough](https://www.redhat.com/en/blog)

### Pipeline patterns

- [DEV.to — time-travel debugging a banking loan-rejection agent](https://dev.to)
- [Redis distributed lock pattern (Lua script)](https://redis.io/docs/manual/patterns/distributed-locks/)

### Cross-references in the curriculum

| S9 section | Builds on |
|---|---|
| Part 2 (LangGraph mental model) | S8 §3 (the hand-rolled loop you're replacing) |
| Part 3.2 (`interrupt()`) | S8 §5.5 (HITL 4-tier taxonomy) |
| Part 3.3 (Time travel) | S8 §7 (observability — this is the production answer) |
| Part 4 (Tool-RAG) | S8 §6.4 (tool description length tradeoff) |
| Part 5 (Worker pipeline) | S7 §4 (Streamable HTTP transport) and S7 §6 (security model) |

### Hands-on companion — `deepbrief-lab`

| Notebook | S9 sections covered |
|---|---|
| `07` (existing) | Part 1 — baseline supervisor/specialists pattern (hand-rolled) |
| `07b` (new) | Part 2 (LangGraph mental model) + Part 3 (three primitives) |
| `08` upgrade | Part 3.1 (`SqliteSaver`) + Part 3.2 (`interrupt()`) + Part 3.3 (`get_state_history`) |
| `09` (new) | Part 5 (worker pipeline — Redis Streams + SSE) |

### Hands-on companion — `voyageai-python-service`

| Module | S9 sections covered |
|---|---|
| `src/voyageai/rag/tool_rag.py` | Part 4 (Tool-RAG) — full production implementation already in place |
| `src/voyageai/rag/seed_definitions.py` | Part 4.5 (example-queries pattern) |
| `src/voyageai/services/agent_types.py` | Part 4.3 (skip threshold) + Part 4.4 (always-include core) |
| `src/voyageai/kafka/worker.py` + `src/voyageai/storage/mongodb.py` | Part 5 (worker pipeline — Kafka + MongoDB version) |

---

— end of S9 —
