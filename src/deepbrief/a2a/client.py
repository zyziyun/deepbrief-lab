"""A2A client — discover an agent by URL, send it tasks.

Two functions:
    fetch_agent_card(url) -> AgentCard
    send_task(url, text) -> reply_text

Plus a one-shot helper that does both:
    delegate(url, text) -> reply_text
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx

from deepbrief.a2a.agent_card import AgentCard


class A2AError(Exception):
    pass


async def fetch_agent_card(base_url: str) -> AgentCard:
    """Fetch /.well-known/agent-card.json and parse as an AgentCard."""
    url = base_url.rstrip("/") + "/.well-known/agent-card.json"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(url)
        r.raise_for_status()
    return AgentCard.model_validate(r.json())


async def send_task(base_url: str, user_text: str, timeout: float = 90.0) -> str:
    """Send a `tasks/send` JSON-RPC call and return the agent's reply text."""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tasks/send",
        "params": {
            "message": {"role": "user", "parts": [{"text": user_text}]},
        },
    }
    url = base_url.rstrip("/") + "/a2a"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise A2AError(f"{body['error']['code']}: {body['error']['message']}")
    msgs = body["result"]["task"]["messages"]
    # Last agent message
    for m in reversed(msgs):
        if m.get("role") == "agent":
            return "".join(p.get("text", "") for p in m.get("parts", []) if "text" in p)
    raise A2AError("Task completed but no agent message found")


async def delegate(base_url: str, user_text: str) -> str:
    """Convenience: discover the card (skip if you don't need it) then send."""
    await fetch_agent_card(base_url)  # warm up + verify it's an A2A agent
    return await send_task(base_url, user_text)
