"""ProgressEmitter — Redis Pub/Sub progress events from S9 §5.3.

Long-running agents (10-60s) need progress signals so the UI doesn't look
frozen. Two common backends: Redis Pub/Sub (lightweight, ephemeral) or Kafka
topic (durable, replayable). We use Pub/Sub here because it's free with the
Redis we already have for the lock.

Event shape (by convention, not enforced by anything):
    {
        "step": int,
        "kind": "thought" | "tool_call" | "tool_result" | "final",
        "ts":   float,         # epoch seconds
        ...payload             # kind-specific fields
    }

Each task has its own channel `agent:progress:{task_id}`. SSE handlers
subscribe to one channel per task.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def channel_for(task_id: str) -> str:
    return f"agent:progress:{task_id}"


class ProgressEmitter:
    """Publish progress events for one task. Cheap to construct per-task."""

    def __init__(self, redis: Redis, task_id: str) -> None:
        self.redis = redis
        self.task_id = task_id
        self.channel = channel_for(task_id)

    async def emit(self, step: int, kind: str, **payload) -> int:
        """Publish one event. Returns subscriber count (0 if nobody's listening)."""
        event = {"step": step, "kind": kind, "ts": time.time(), **payload}
        return await self.redis.publish(self.channel, json.dumps(event))


async def subscribe(redis: Redis, task_id: str) -> AsyncIterator[dict]:
    """Yield progress events for one task as they arrive.

    Caller is responsible for breaking out of the loop on `kind == "final"`
    (or on a timeout). The subscription is closed when the generator is
    garbage-collected.
    """
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel_for(task_id))
    try:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            yield json.loads(msg["data"])
    finally:
        await pubsub.unsubscribe(channel_for(task_id))
        await pubsub.close()
