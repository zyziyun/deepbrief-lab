"""Tests for the A2A layer — AgentCard and the FastAPI server.

We use httpx.AsyncClient with ASGITransport to test the server in-process —
no real port binding, no subprocess, no network. Faster and deterministic.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from deepbrief.a2a.agent_card import AgentCard, researcher_card
from deepbrief.a2a.server import make_a2a_app


# ─────────────────────────────────────────────────────────────────────────────
# AgentCard
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentCard:
    def test_researcher_card_has_skill(self):
        card = researcher_card("http://localhost:9001")
        assert card.name == "DeepBrief Researcher"
        assert card.url == "http://localhost:9001"
        assert any(s.id == "research_subquery" for s in card.skills)

    def test_card_serialises_to_json(self):
        card = researcher_card("http://x")
        data = card.model_dump()
        assert data["url"] == "http://x"
        assert "skills" in data
        # Round-trip
        parsed = AgentCard.model_validate(data)
        assert parsed.url == card.url


# ─────────────────────────────────────────────────────────────────────────────
# A2A server
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def a2a_client():
    """Build an A2A app with an echo handler and yield an in-process AsyncClient."""

    async def echo_handler(text: str) -> str:
        return f"received: {text}"

    card = researcher_card("http://test")
    app = make_a2a_app(card, echo_handler)
    return ASGITransport(app=app)


class TestA2AServer:
    @pytest.mark.asyncio
    async def test_get_agent_card(self, a2a_client):
        async with AsyncClient(transport=a2a_client, base_url="http://test") as ac:
            r = await ac.get("/.well-known/agent-card.json")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "DeepBrief Researcher"
        assert "skills" in data

    @pytest.mark.asyncio
    async def test_tasks_send_round_trip(self, a2a_client):
        payload = {
            "jsonrpc": "2.0",
            "id": "abc-1",
            "method": "tasks/send",
            "params": {
                "message": {"role": "user", "parts": [{"text": "hello"}]},
            },
        }
        async with AsyncClient(transport=a2a_client, base_url="http://test") as ac:
            r = await ac.post("/a2a", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "abc-1"
        assert "result" in body
        task = body["result"]["task"]
        assert task["state"] == "completed"
        # Last message is the agent's reply
        agent_msg = task["messages"][-1]
        assert agent_msg["role"] == "agent"
        assert agent_msg["parts"][0]["text"] == "received: hello"

    @pytest.mark.asyncio
    async def test_tasks_get(self, a2a_client):
        async with AsyncClient(transport=a2a_client, base_url="http://test") as ac:
            send = await ac.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0", "id": 1, "method": "tasks/send",
                    "params": {"message": {"role": "user", "parts": [{"text": "hi"}]}},
                },
            )
            task_id = send.json()["result"]["task"]["id"]

            got = await ac.post(
                "/a2a",
                json={"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": task_id}},
            )

        assert got.status_code == 200
        body = got.json()
        assert body["result"]["task"]["id"] == task_id

    @pytest.mark.asyncio
    async def test_unknown_method_returns_error(self, a2a_client):
        async with AsyncClient(transport=a2a_client, base_url="http://test") as ac:
            r = await ac.post(
                "/a2a",
                json={"jsonrpc": "2.0", "id": 99, "method": "nope/nope", "params": {}},
            )
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_handler_exception_becomes_jsonrpc_error(self):
        async def boom_handler(text: str) -> str:
            raise RuntimeError("handler failed on purpose")

        card = researcher_card("http://test")
        app = make_a2a_app(card, boom_handler)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0", "id": 7, "method": "tasks/send",
                    "params": {"message": {"role": "user", "parts": [{"text": "x"}]}},
                },
            )
        body = r.json()
        assert "error" in body
        assert "handler failed on purpose" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_missing_text_returns_error(self, a2a_client):
        async with AsyncClient(transport=a2a_client, base_url="http://test") as ac:
            r = await ac.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0", "id": 5, "method": "tasks/send",
                    "params": {"message": {"role": "user", "parts": []}},
                },
            )
        body = r.json()
        assert "error" in body
