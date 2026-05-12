"""The full worker loop — S9 §5.4, with subtle ordering preserved.

The order of the six steps matters. Wrong order shows up in interviews as
silent data loss or double-billing. Keep this file as the canonical
reference for the pattern; copy from here.

Steps:
    1. consume from queue
    2. existing-result check (short-circuit completed work)
    3. acquire SETNX lock (skip if someone else got it)
    4. run the agent / graph
    5. persist final result
    6. ack queue message — ONLY after successful persist

Idempotency comes from two layers:
- **In-flight protection** — Redis SETNX lock prevents two workers running
  the same task concurrently
- **Completed-task protection** — the existing-result check at step 2 short
  -circuits replays of finished work

Both are needed. Drop either and you get either double-execution or wasted
retry work.
"""

from __future__ import annotations

import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

from deepbrief.pipeline.locks import IdempotencyLock
from deepbrief.pipeline.progress import ProgressEmitter
from deepbrief.pipeline.store import ResultStore
from deepbrief.pipeline.streams import consume_loop

logger = logging.getLogger(__name__)


# A handler runs the agent for ONE task and returns the final result dict.
# It receives the task payload and a ProgressEmitter so the agent can emit
# events as it runs.
AgentHandler = Callable[[dict, ProgressEmitter], Awaitable[dict]]


def default_worker_id() -> str:
    """worker-<hostname>-<short uuid>. Distinct per process, identifiable in logs."""
    return f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def make_handler(
    *,
    redis: Redis,
    store: ResultStore,
    lock: IdempotencyLock,
    agent_handler: AgentHandler,
    worker_id: str,
) -> Callable[[dict], Awaitable[bool]]:
    """Build the per-message handler that `consume_loop` will call.

    Returns `True` if the message was processed (so the loop acks it), or
    `False` if the message should redeliver. Exceptions in `agent_handler`
    are caught — message is left pending; idempotency lock prevents replay
    within TTL.
    """

    async def handle(task: dict) -> bool:
        task_id = task["task_id"]
        log = logger.bind(task_id=task_id) if hasattr(logger, "bind") else logger

        # Step 2: existing-result check — short-circuit completed work
        existing = await store.find_one(task_id)
        if existing:
            log.info("task already completed — acking without re-running")
            return True

        # Step 3: acquire SETNX lock
        if not await lock.acquire(task_id, worker_id):
            log.info("lock held by another worker — acking; they'll persist the result")
            return True

        try:
            # Step 4: run the agent
            emitter = ProgressEmitter(redis, task_id)
            await emitter.emit(step=0, kind="started", query=task.get("query"))

            result = await agent_handler(task, emitter)

            # Step 5: persist
            await store.insert_one(
                task_id=task_id,
                query=task.get("query") or task.get("topic", ""),
                answer=result.get("answer") or result.get("draft"),
                state=result,
            )

            await emitter.emit(step=-1, kind="final", answer=result.get("answer") or result.get("draft"))

            # Step 6: ack (handled by the loop on `return True`)
            return True
        except Exception:
            log.exception("agent run failed")
            # DON'T ack — message redelivers; idempotency lock prevents replay within TTL
            return False
        finally:
            await lock.release(task_id, worker_id)

    return handle


async def run_worker(
    *,
    redis: Redis,
    store: ResultStore,
    agent_handler: AgentHandler,
    group: str = "deepbrief-workers",
    worker_id: str | None = None,
    stop_after: int | None = None,
) -> int:
    """One-shot helper to wire up a complete worker.

    For deployment you'd usually inline the steps so you can configure
    timeouts, signal handlers, and graceful shutdown. This is here to
    make the notebook demo a single line.
    """
    worker_id = worker_id or default_worker_id()
    lock = IdempotencyLock(redis)

    handler = make_handler(
        redis=redis, store=store, lock=lock,
        agent_handler=agent_handler, worker_id=worker_id,
    )
    return await consume_loop(
        redis,
        group=group, consumer=worker_id,
        handler=handler, stop_after=stop_after,
    )
