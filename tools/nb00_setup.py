"""Generator for notebooks/00_setup.ipynb."""

from build_notebook import code, md, write

cells = [
    md(
        "# 00 — Setup\n"
        "\n"
        "Welcome to **DeepBrief** — a hands-on lab where you'll build your own minimal Deep Research clone.\n"
        "\n"
        "## What you'll build by the end\n"
        "\n"
        "A multi-agent system that takes a topic and produces a structured research brief with citations:\n"
        "\n"
        "```\n"
        "you: \"State of WebGPU adoption in 2026\"\n"
        "      ↓\n"
        "  Coordinator → decomposes into sub-questions\n"
        "      ↓ (A2A)\n"
        "  Researchers → run in parallel, call MCP tools (search + fetch)\n"
        "      ↓\n"
        "  Synthesizer → merges findings\n"
        "      ↓\n"
        "  Editor (HITL) → you approve before saving\n"
        "      ↓\n"
        "  brief.md with citations\n"
        "```\n"
        "\n"
        "## What this notebook does\n"
        "\n"
        "This notebook only verifies your environment. **No agent code yet.** The fun starts in 01.\n"
        "\n"
        "## Prereqs (one-time)\n"
        "\n"
        "1. Python 3.11+\n"
        "2. `uv sync` (or `pip install -e .`)\n"
        "3. Copy `.env.example` to `.env` and fill in:\n"
        "   - `OPENAI_API_KEY` — required from notebook 01\n"
        "   - `TAVILY_API_KEY` — required from notebook 04 ([free tier signup](https://tavily.com))\n"
    ),
    md("## 1. Verify environment variables"),
    code(
        "import os\n"
        "from dotenv import load_dotenv\n"
        "\n"
        "load_dotenv()\n"
        "\n"
        "openai_ok = bool(os.getenv(\"OPENAI_API_KEY\"))\n"
        "tavily_ok = bool(os.getenv(\"TAVILY_API_KEY\"))\n"
        "\n"
        "print(f\"OPENAI_API_KEY:  {'✅ set' if openai_ok else '❌ missing — required for notebook 01+'}\")\n"
        "print(f\"TAVILY_API_KEY:  {'✅ set' if tavily_ok else '⚠️  missing — required for notebook 04+'}\")\n"
        "\n"
        "assert openai_ok, \"Set OPENAI_API_KEY in .env before continuing.\""
    ),
    md(
        "## 2. Test the OpenAI connection\n"
        "\n"
        "If this cell fails, it's almost always one of:\n"
        "- Wrong key (typos, expired)\n"
        "- Network issue (corporate proxy, firewall)\n"
        "- Out of credits"
    ),
    code(
        "from openai import AsyncOpenAI\n"
        "\n"
        "client = AsyncOpenAI()\n"
        "\n"
        "resp = await client.chat.completions.create(\n"
        "    model=os.getenv(\"OPENAI_MODEL\", \"gpt-4o-mini\"),\n"
        "    messages=[{\"role\": \"user\", \"content\": \"Say hi to a DeepBrief student in 8 words or fewer.\"}],\n"
        ")\n"
        "print(resp.choices[0].message.content)\n"
        "print(f\"\\nmodel: {resp.model}  •  tokens: {resp.usage.total_tokens}\")"
    ),
    md(
        "## 3. (Optional) Test Tavily — only needed from notebook 04 onward"
    ),
    code(
        "if tavily_ok:\n"
        "    from tavily import TavilyClient\n"
        "    tv = TavilyClient(api_key=os.getenv(\"TAVILY_API_KEY\"))\n"
        "    r = tv.search(\"What is the Model Context Protocol\", max_results=2)\n"
        "    for hit in r[\"results\"][:2]:\n"
        "        print(\"•\", hit[\"title\"])\n"
        "        print(\"  \", hit[\"url\"])\n"
        "else:\n"
        "    print(\"Skipped — set TAVILY_API_KEY before notebook 04.\")"
    ),
    md(
        "## What's next\n"
        "\n"
        "| Notebook | What you'll learn |\n"
        "|---|---|\n"
        "| **01** | The 6-line agent loop, then production-hardened |\n"
        "| 02 | `BaseTool` / `ToolRegistry` and OpenAI strict mode |\n"
        "| 03 | Termination caps, loop fingerprinting, cost meter |\n"
        "| 04 | Build your first MCP server with FastMCP |\n"
        "| 05 | stdio vs Streamable HTTP transport |\n"
        "| 06 | Wire MCP tools into the agent |\n"
        "| 07 | Multi-agent + A2A protocol |\n"
        "| 08 | Capstone: full DeepBrief with HITL editor |\n"
        "\n"
        "**Ready?** Open `01_agent_loop.ipynb`."
    ),
]

write("notebooks/00_setup.ipynb", cells)
print("wrote notebooks/00_setup.ipynb")
