"""Minimal A2A server — JSON-RPC 2.0 over HTTP, plus the AgentCard discovery doc.

Real A2A supports streaming (SSE), push notifications (webhook callbacks),
and stateful multi-turn tasks (input-required). For the lab we only implement
**synchronous `tasks/send`** — submit a message, get a final response. That's
~80% of what people actually use.

Endpoints:
    GET  /.well-known/agent-card.json   → AgentCard JSON
    POST /a2a                            → JSON-RPC 2.0
        method: "tasks/send"
        params: {"message": {"role": "user", "parts": [{"text": "..."}]}}
        result: {"task": {"id": "...", "state": "completed",
                          "messages": [..., {"role": "agent", "parts": [{"text": "..."}]}]}}

"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from deepbrief.a2a.agent_card import AgentCard

logger = logging.getLogger(__name__)


# Type for the function the user provides to handle a task.
# Receives the user message text, returns the agent's reply text.
TaskHandler = Callable[[str], Awaitable[str]]


def make_a2a_app(card: AgentCard, handler: TaskHandler) -> FastAPI:
    """Build a FastAPI app exposing one A2A agent."""
    app = FastAPI(title=card.name)

    # In-memory task store. Real impl would use durable storage.
    _tasks: dict[str, dict[str, Any]] = {}

    @app.get("/.well-known/agent-card.json")
    async def get_agent_card() -> dict:
        return card.model_dump()

    @app.post("/a2a")
    async def jsonrpc(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception as e:
            return JSONResponse(_jsonrpc_error(None, -32700, f"Parse error: {e}"))

        rpc_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}

        if method == "tasks/send":
            return JSONResponse(await _handle_tasks_send(rpc_id, params))
        if method == "tasks/get":
            return JSONResponse(_handle_tasks_get(rpc_id, params))

        return JSONResponse(_jsonrpc_error(rpc_id, -32601, f"Method not found: {method}"))

    async def _handle_tasks_send(rpc_id: Any, params: dict) -> dict:
        message = params.get("message", {})
        text = _extract_text(message)
        if not text:
            return _jsonrpc_error(rpc_id, -32602, "message.parts must include {text: ...}")

        task_id = str(uuid.uuid4())
        _tasks[task_id] = {"id": task_id, "state": "working", "messages": [message]}
        try:
            reply_text = await handler(text)
        except Exception as e:
            logger.exception("handler failed")
            _tasks[task_id]["state"] = "failed"
            return _jsonrpc_error(rpc_id, -32000, f"Agent handler error: {e}")

        agent_message = {"role": "agent", "parts": [{"text": reply_text}]}
        _tasks[task_id]["messages"].append(agent_message)
        _tasks[task_id]["state"] = "completed"

        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"task": _tasks[task_id]}}

    def _handle_tasks_get(rpc_id: Any, params: dict) -> dict:
        task_id = params.get("id")
        task = _tasks.get(task_id)
        if not task:
            return _jsonrpc_error(rpc_id, -32001, f"Task {task_id} not found")
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"task": task}}

    return app


def _extract_text(message: dict) -> str:
    return "".join(p.get("text", "") for p in message.get("parts", []) if "text" in p)


def _jsonrpc_error(rpc_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
