"""FastAPI surface — POST /runs to enqueue, GET /runs/{id}/stream for SSE.

This is what you'd deploy. Notebook 09 demonstrates the same primitives
in-process without uvicorn — but the deploy-ready code lives here.

Run with:
    uvicorn deepbrief.pipeline.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse

from deepbrief.pipeline.progress import subscribe
from deepbrief.pipeline.store import ResultStore
from deepbrief.pipeline.streams import enqueue


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RUNS_DB_PATH = os.getenv("RUNS_DB_PATH", "./data/runs.db")


app = FastAPI(title="DeepBrief Pipeline API")
_redis: Redis | None = None
_store: ResultStore | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _redis, _store
    _redis = await Redis.from_url(REDIS_URL)
    _store = ResultStore(RUNS_DB_PATH)


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _redis, _store
    if _redis is not None:
        await _redis.close()
    if _store is not None:
        _store.close()


class CreateRun(BaseModel):
    topic: str
    user_id: str | None = None
    task_id: str | None = None      # caller-supplied = stable retries


class RunEnvelope(BaseModel):
    task_id: str
    message_id: str


@app.post("/runs", response_model=RunEnvelope)
async def create_run(req: CreateRun) -> RunEnvelope:
    """Producer endpoint: enqueue a research task, return its task_id.

    Decoupled from the worker — this returns as soon as Redis accepts the
    write. The browser can close; the worker keeps running.
    """
    if _redis is None:
        raise HTTPException(503, "pipeline not initialized")
    task: dict[str, Any] = {"topic": req.topic, "query": req.topic}
    if req.task_id:
        task["task_id"] = req.task_id
    if req.user_id:
        task["user_id"] = req.user_id
    msg_id = await enqueue(_redis, task)
    return RunEnvelope(task_id=task["task_id"], message_id=msg_id)


@app.get("/runs/{task_id}")
async def get_run(task_id: str) -> dict:
    """Result endpoint: returns the stored run, or 404 if not yet completed."""
    if _store is None:
        raise HTTPException(503, "pipeline not initialized")
    rec = await _store.find_one(task_id)
    if not rec:
        raise HTTPException(404, f"task {task_id} not found")
    return rec


@app.get("/runs/{task_id}/stream")
async def stream_progress(task_id: str) -> EventSourceResponse:
    """SSE endpoint: stream progress events as the worker emits them.

    The browser receives a continuous stream of JSON events. Disconnect
    behavior: when the client closes, the async generator is GC'd and the
    pub/sub subscription is cleaned up in `progress.subscribe`'s `finally`.
    """
    if _redis is None:
        raise HTTPException(503, "pipeline not initialized")

    async def event_stream():
        async for event in subscribe(_redis, task_id):
            yield {"data": str(event)}
            if event.get("kind") == "final":
                break

    return EventSourceResponse(event_stream())
