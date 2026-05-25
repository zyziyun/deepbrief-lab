"""Generator for notebooks/06_mcp_in_agent.ipynb."""

from build_notebook import code, md, write

cells = [
    md(
        "# 06 — Wire MCP Tools Into the Agent\n"
        "\n"
        "We have:\n"
        "- An agent loop with `ReActAgent` and a `ToolRegistry` (notebooks 01-03)\n"
        "- Two MCP servers: `notes_server` (stdio + HTTP) and `cache_server` (HTTP) (notebooks 04-05)\n"
        "\n"
        "Now we connect them. The goal is that **the agent loop has no idea** whether a tool is local "
        "Python or remote MCP — they all look the same to the loop.\n"
        "\n"
        "**By the end of this notebook you will:**\n"
        "1. Build an `MCPToolAdapter` that wraps an MCP tool as a `BaseTool`\n"
        "2. Register a mix of native tools + MCP tools into one `ToolRegistry`\n"
        "3. Run a real research mini-agent: search the web → fetch a page → save findings as MCP notes\n"
        "4. See namespacing (`notes__save_note`) prevent tool-name collisions\n"
    ),
    md(
        "## 1. Read the adapter\n"
        "\n"
        "`MCPToolAdapter` lives at `src/deepbrief/tools/mcp_adapter.py`. The pattern: subclass `BaseTool`, "
        "and in `execute()` open a fresh MCP session, call the tool, parse the response."
    ),
    code(
        "import inspect\n"
        "from deepbrief.tools.mcp_adapter import MCPToolAdapter, discover_http_tools, _strictify\n"
        "\n"
        "print(inspect.getsource(MCPToolAdapter))"
    ),
    md(
        "## 2. Start the MCP servers\n"
        "\n"
        "Same as notebook 05 — we'll run both `notes_server` (HTTP) and `cache_server` (HTTP) as background "
        "subprocesses. Make sure the cells from earlier are stopped first.\n"
    ),
    code(
        "import os, subprocess, sys, time\n"
        "\n"
        "subprocess.run([\"pkill\", \"-f\", \"deepbrief.mcp_servers\"], capture_output=True)\n"
        "time.sleep(1)\n"
        "\n"
        "notes_proc = subprocess.Popen(\n"
        "    [sys.executable, \"-m\", \"deepbrief.mcp_servers.notes_server\", \"--http\"],\n"
        "    stdout=subprocess.PIPE, stderr=subprocess.PIPE,\n"
        "    env={**os.environ, \"MCP_PORT\": \"8766\"},\n"
        ")\n"
        "cache_proc = subprocess.Popen(\n"
        "    [sys.executable, \"-m\", \"deepbrief.mcp_servers.cache_server\"],\n"
        "    stdout=subprocess.PIPE, stderr=subprocess.PIPE,\n"
        "    env={**os.environ, \"PORT\": \"8765\"},\n"
        ")\n"
        "time.sleep(2)\n"
        "for p, name in [(notes_proc, \"notes\"), (cache_proc, \"cache\")]:\n"
        "    print(f\"{name}_server: pid={p.pid}  alive={p.poll() is None}\")"
    ),
    md(
        "## 3. Discover MCP tools and register them\n"
        "\n"
        "`discover_http_tools` connects to the server, lists its tools, returns one `MCPToolAdapter` per tool. "
        "We register them in our `ToolRegistry` alongside the native `WebSearchTool` and `FetchURLTool`."
    ),
    code(
        "from dotenv import load_dotenv\n"
        "load_dotenv()\n"
        "\n"
        "from deepbrief.tools.registry import ToolRegistry\n"
        "from deepbrief.tools.web_search import WebSearchTool, MockSearchTool\n"
        "from deepbrief.tools.fetch_url import FetchURLTool\n"
        "\n"
        "# Use real search if Tavily key set, mock otherwise\n"
        "search_tool = WebSearchTool() if os.getenv(\"TAVILY_API_KEY\") else MockSearchTool()\n"
        "print(f\"Search backend: {type(search_tool).__name__}\")\n"
        "\n"
        "registry = ToolRegistry()\n"
        "registry.register(search_tool)\n"
        "registry.register(FetchURLTool())\n"
        "\n"
        "# Discover MCP tools and namespace them\n"
        "for adapter in await discover_http_tools(\"http://localhost:8766/mcp\", \"notes\"):\n"
        "    registry.register(adapter)\n"
        "\n"
        "for adapter in await discover_http_tools(\"http://localhost:8765/mcp\", \"cache\"):\n"
        "    registry.register(adapter)\n"
        "\n"
        "print(\"\\nAll tools in registry:\")\n"
        "for name in registry.list_tools():\n"
        "    print(f\"  • {name}\")"
    ),
    md(
        "Notice the **namespacing**: `notes__save_note` and `cache__cache_get`. The native tools "
        "(`web_search`, `fetch_url`) keep simple names because we own them. If we hadn't namespaced, "
        "two MCP servers exposing a `search` tool would collide — and we'd silently call the wrong one. "
        "Tool-name shadowing is a real attack class.\n"
    ),
    md(
        "## 4. Verify the adapter works end-to-end"
    ),
    code(
        "# Call an MCP tool through our registry, just like a native tool\n"
        "result = await registry.execute(\"notes__save_note\", {\n"
        "    \"title\": \"Test from notebook 06\",\n"
        "    \"content\": \"This note was created via the MCPToolAdapter.\",\n"
        "})\n"
        "print(\"save_note via registry:\", result.output, \"  latency_ms:\", result.latency_ms)\n"
        "\n"
        "result = await registry.execute(\"notes__list_notes\", {})\n"
        "print(\"\\nlist_notes:\")\n"
        "for note in result.output[:3]:   # first 3\n"
        "    print(\"  -\", note)"
    ),
    md(
        "## 5. Run the agent with mixed tools\n"
        "\n"
        "Now the magic: hand the registry to `ReActAgent` and ask it to do a real research task. The "
        "agent doesn't know which tools are local Python and which are MCP — they all look the same."
    ),
    code(
        "from deepbrief.agents.react import ReActAgent\n"
        "\n"
        "SYSTEM_PROMPT = \"\"\"You are a research assistant building notes for a brief.\n"
        "\n"
        "# Workflow\n"
        "1. Use `web_search` to find sources.\n"
        "2. For the most relevant 1-2 results, call `fetch_url` to read the page.\n"
        "3. Save concise findings via `notes__save_note`. Include the source URL in the content.\n"
        "4. When done, list saved notes via `notes__list_notes` and write a brief summary.\n"
        "\n"
        "# Constraints\n"
        "- Make at most 3 web_search calls and 2 fetch_url calls.\n"
        "- Save at most 3 notes total.\n"
        "- If a tool fails, do not retry more than once.\n"
        "\"\"\"\n"
        "\n"
        "agent = ReActAgent(\n"
        "    registry=registry,\n"
        "    system_prompt=SYSTEM_PROMPT,\n"
        "    max_steps=10,\n"
        ")\n"
        "result = await agent.run(\"What is the Model Context Protocol (MCP) and who created it?\")\n"
        "\n"
        "print(\"=== ANSWER ===\")\n"
        "print(result.answer)\n"
        "print(f\"\\nsteps={result.steps}  terminated_by={result.terminated_by}\")\n"
        "print(\"\\n=== TRACE ===\")\n"
        "for entry in result.trace:\n"
        "    calls = entry['tool_calls'] or '—'\n"
        "    print(f\"  step {entry['step']}: {calls}\")"
    ),
    md(
        "Look at the trace — you should see a mix of native tool calls (`web_search`, `fetch_url`) and "
        "MCP tool calls (`notes__save_note`, `notes__list_notes`). The agent treats them identically.\n"
    ),
    md(
        "## 6. Verify the notes ended up on disk\n"
        "\n"
        "Notes are persisted as JSON files in `./data/notes/` (gitignored). The cache lives in-memory in "
        "the cache_server process — restarting the cache_server clears it."
    ),
    code(
        "from pathlib import Path\n"
        "import json\n"
        "\n"
        "notes_dir = Path(\"./data/notes\")\n"
        "if notes_dir.exists():\n"
        "    for path in sorted(notes_dir.glob(\"*.json\"))[-5:]:\n"
        "        data = json.loads(path.read_text())\n"
        "        print(f\"📝 {data['title']}  ({data['id']})\")\n"
        "        print(f\"   {data['content'][:150]}...\\n\")"
    ),
    md(
        "## 7. Cleanup"
    ),
    code(
        "for proc, name in [(notes_proc, \"notes\"), (cache_proc, \"cache\")]:\n"
        "    proc.terminate()\n"
        "    try:\n"
        "        proc.wait(timeout=5)\n"
        "    except subprocess.TimeoutExpired:\n"
        "        proc.kill()\n"
        "    print(f\"{name}_server stopped\")"
    ),
    md(
        "## 8. Self-check\n"
        "\n"
        "1. Why does `MCPToolAdapter.execute()` open a *fresh* session per call instead of reusing one?\n"
        "2. What's the namespacing convention and what attack does it defend against?\n"
        "3. The agent calls `notes__save_note`. Walk through what happens on the wire (which transport, "
        "which JSON-RPC methods).\n"
        "4. Why do we patch `additionalProperties: False` onto MCP-discovered schemas (`_strictify`)?\n"
        "\n"
        "## What's next\n"
        "\n"
        "Notebook **07** — we go horizontal. Instead of one big agent, build a **coordinator + specialist** "
        "team that talks via the **A2A protocol**. Each agent has its own LLM, its own tool stack, and its "
        "own little loop. Coordinator uses A2A to delegate; each specialist uses MCP to access tools."
    ),
]

write("notebooks/06_mcp_in_agent.ipynb", cells)
print("wrote notebooks/06_mcp_in_agent.ipynb")
