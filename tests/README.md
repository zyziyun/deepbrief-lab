# Tests

Unit tests for the DeepBrief library code under `src/deepbrief/`. **No API keys, no network, no subprocesses required** — every test runs in-process.

```
tests/
├── conftest.py             — shared fixtures (EchoTool, ExplodingTool)
├── test_tools.py           — BaseTool, ToolResult, ToolRegistry, MockSearchTool, _strictify
├── test_guards.py          — CostMeter, LoopGuard
├── test_editor.py          — ApprovalStore (file-backed approval queue)
├── test_a2a.py             — AgentCard + A2A FastAPI server (in-process via ASGI)
└── test_mcp_servers.py     — notes_server + cache_server tool functions
```

51 tests total · runs in ~1.5 seconds.

## What's NOT tested here

Tests deliberately skip components that need real services:

| Component | Why skipped | Coverage strategy |
|---|---|---|
| `ReActAgent.run()` | Needs an LLM | Verified by running notebooks 01-08 |
| `Coordinator.run()` | Needs LLM + running researchers | Verified by notebooks 07-08 |
| `WebSearchTool` | Needs Tavily API | `MockSearchTool` is tested instead |
| `FetchURLTool` | Needs network | Skipped — straight httpx wrapper |
| MCP transport plumbing | FastMCP / `mcp` SDK already covers it | We test our tool functions directly |

If you want LLM-touching integration tests, add them under `tests/integration/` and gate with a marker — that's the conventional split.

## How to run

### One-time setup

```bash
# Install all deps including pytest, pytest-asyncio, ruff
uv sync --extra dev

# (Or with pip)
pip install -e ".[dev]"
```

You do **not** need `.env` set up to run tests. Tests don't read environment variables.

### Run everything

```bash
uv run pytest
```

Expected output:

```
....................................................... [100%]
51 passed in 1.18s
```

### Run with verbose output (see every test name)

```bash
uv run pytest -v
```

### Run one file

```bash
uv run pytest tests/test_guards.py
uv run pytest tests/test_a2a.py -v
```

### Run one test class or one test

```bash
# By class
uv run pytest tests/test_editor.py::TestApprovalStore

# By full nodeid
uv run pytest tests/test_editor.py::TestApprovalStore::test_decide_edited_uses_new_markdown
```

### Filter by name pattern

```bash
# Anything with "approve" in the name
uv run pytest -k approve

# Anything that's NOT in test_a2a
uv run pytest -k "not a2a"
```

### Show output from `print()` calls

By default pytest captures stdout. To see your prints (useful when debugging a failing test):

```bash
uv run pytest -s
```

### Stop on first failure

```bash
uv run pytest -x
```

### Re-run only the failures from last run

```bash
uv run pytest --lf
```

### Coverage report

```bash
uv run pytest --cov=deepbrief --cov-report=term-missing
```

(Requires `pytest-cov` — add it to your dev extras if you want it: `pip install pytest-cov`.)

## How the tests are structured

Three patterns to know:

### 1. Pure unit tests (most files)

Tests instantiate a class, call a method, assert on the return.

```python
def test_threshold_trips(self):
    g = LoopGuard(repeat_threshold=3)
    assert not g.is_looping("t", {"x": 1})    # 1st
    assert not g.is_looping("t", {"x": 1})    # 2nd
    assert g.is_looping("t", {"x": 1})        # 3rd ← trip
```

### 2. Async tests (`test_editor.py`, `test_a2a.py`, parts of `test_tools.py`)

Decorated with `@pytest.mark.asyncio`. `pyproject.toml` has `asyncio_mode = "auto"` so any `async def test_...` is run as a coroutine.

```python
@pytest.mark.asyncio
async def test_execute_success_path(self, echo_tool):
    r = ToolRegistry()
    r.register(echo_tool)
    result = await r.execute("echo", {"text": "hi"})
    assert result.success is True
```

### 3. ASGI in-process server tests (`test_a2a.py`)

Instead of spinning up uvicorn on a real port, we hand the FastAPI app to `httpx.AsyncClient` via `ASGITransport`. Same HTTP semantics, no real socket, no race conditions.

```python
transport = ASGITransport(app=app)
async with AsyncClient(transport=transport, base_url="http://test") as ac:
    r = await ac.post("/a2a", json=payload)
```

This is the production-grade FastAPI testing pattern — reuse it whenever you write FastAPI code.

## Common gotchas

- **`ModuleNotFoundError: No module named 'deepbrief'`** — you didn't run `uv sync` (or `pip install -e .`). The package needs to be installed in editable mode.
- **`fixture 'event_loop' not found`** — you're using a too-old `pytest-asyncio`. Make sure you have `>=0.23`.
- **Tests pass locally but fail in CI** — check that CI installs the `dev` extra, not just the base deps.
- **Tests for `notes_server` mutate `./data/notes/`** — they don't. We monkeypatch `NOTES_DIR` to a `tmp_path` per test. If you see real notes appearing, you broke the fixture.

## Adding new tests

1. Pick the right file based on what you're testing — or make a new `test_<area>.py` if it's a new area.
2. If you need a fixture used by multiple files, put it in `conftest.py`.
3. If your test needs an async LLM call or network, **don't add it here** — it'll flake. Make a `tests/integration/` directory and gate it with a `@pytest.mark.integration` marker.

## Pre-commit (optional but recommended)

If you want tests to run automatically on every commit:

```bash
# .git/hooks/pre-commit  (chmod +x)
#!/bin/sh
uv run pytest -q || {
    echo "Tests failed. Use 'git commit --no-verify' to bypass."
    exit 1
}
```
