# DeepBrief — Build Your Own Deep Research

A hands-on lab for students learning to build production-grade LLM agents. You'll build a minimal **Deep Research clone** — a multi-agent system that takes a topic and produces a structured brief with citations — across 9 self-contained Jupyter notebooks.

This lab is the practical companion to lectures **S7 (MCP & Agent Protocols)** and **S8 (Agents: Foundations)**.

## What You'll Build

By the end, `python -m deepbrief.run` accepts a topic and produces a `brief.md`:

```
You: "State of WebGPU adoption in 2026"
        ↓
Coordinator agent     decomposes into 3-5 sub-questions
        ↓ (A2A)
Researcher agents     run in parallel; each calls web_search + fetch_url MCP tools
        ↓
Synthesizer agent     merges findings into a draft brief
        ↓
Editor (HITL gate)    you approve / reject / edit before it's saved
        ↓
brief.md              with [1][2] citations and a sources list
```

## Notebook → Lecture Map

| # | Notebook | Lecture sections covered |
|---|---|---|
| 00 | Setup | — |
| 01 | The Agent Loop | S8 §3 |
| 02 | Tools & Strict Mode | S8 §3.5, §6 |
| 03 | Termination & Cost | S8 §4 |
| 04 | FastMCP Server | S7 §5 |
| 05 | MCP Transports | S7 §4 |
| 06 | MCP in the Agent | S7 §3 |
| 07 | Multi-Agent + A2A | S7 §7 |
| 08 | HITL Capstone | S8 §5.5 |

Each notebook is independent — you can jump in anywhere. The capstone (08) wires everything together.

## Prereqs

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or pip
- An OpenAI API key (or any OpenAI-compatible endpoint)
- A free [Tavily API key](https://tavily.com) — 1000 searches/month free, needed from notebook 04 onward
- Node.js 18+ — only for the MCP Inspector in notebook 04

## Install

```bash
git clone <this-repo-url>
cd deepbrief-lab
uv sync
cp .env.example .env
# fill in OPENAI_API_KEY and TAVILY_API_KEY in .env
```

Run notebooks:

```bash
uv run jupyter lab notebooks/
```

## Tests

51 unit tests for the library code (`src/deepbrief/`). No API keys, no network — runs in ~1.5 seconds:

```bash
uv sync --extra dev      # one-time
uv run pytest            # run all tests
uv run pytest -v         # show each test name
uv run pytest -k editor  # filter by name
```

See [`tests/README.md`](tests/README.md) for the full guide (how to run a single test, coverage, async/ASGI patterns, etc.).

## Repo Layout

```
deepbrief-lab/
├── notebooks/                      # 9 self-contained Jupyter notebooks
├── src/deepbrief/                  # reusable package — used by capstone
│   ├── tools/                      # BaseTool, ToolRegistry, web_search, fetch_url
│   ├── agents/                     # ReActAgent, coordinator, researcher, editor
│   ├── mcp_servers/                # notes_server (stdio), cache_server (HTTP)
│   └── a2a/                        # AgentCard + JSON-RPC server
└── tests/
```

## Credits

Built as a teaching lab for the VoyageAI Full-Stack AI Engineering course.
Designed by Wendy Yu • Lectures S7/S8 reference material in `docs/`.

## License

MIT
