"""Redis Streams as the durable queue

Streams give us: durable storage (messages survive Redis restart), consumer
groups (multiple workers compete for messages), explicit ack, and dead-letter
patterns via XPENDING / XCLAIM.

We keep the API minimal:
- `enqueue(redis, task)` — producer side; pushes JSON to a stream
- `consume_loop(redis, group, consumer, handler)` — worker side; reads from
  a consumer group, hands each message to a handler, acks on success

The handler returns `True` to ack, `False` to leave the message pending
(redelivers on the next claim).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import ResponseError

logger = logging.getLogger(__name__)


STREAM = "agent:runs"


async def ensure_group(redis: Redis, group: str) -> None:
    """Create the consumer group if it doesn't exist.

    `MKSTREAM` creates the stream too if absent — useful for first deploy.
    `BUSYGROUP` is the harmless error you get if the group already exists.
    """
    try:
        await redis.xgroup_create(STREAM, group, id="0", mkstream=True)
        logger.info("created consumer group: %s", group)
    except ResponseError as e:
        if "BUSYGROUP" in str(e):
            return                  # group already exists — fine
        raise


async def enqueue(redis: Redis, task: dict) -> str:
    """Push one task to the stream. Returns the Redis-assigned message id.

    Each task is required to have a `task_id` for idempotency. If you don't
    supply one we generate a UUID — but production code should pass the
    user's request id so retries get deduped.
    """
    task.setdefault("task_id", uuid.uuid4().hex[:12])
    msg_id = await redis.xadd(STREAM, {"json": json.dumps(task)})
    logger.info("enqueued: task=%s msg=%s", task["task_id"], msg_id)
    return msg_id


async def consume_loop(
    redis: Redis,
    *,
    group: str,
    consumer: str,
    handler: Callable[[dict], Awaitable[bool]],
    stop_after: int | None = None,
    block_ms: int = 5000,
) -> int:
    """Run the worker loop.

    `handler(task)` returns True to ack the message, False to leave it
    pending (redelivers). Exceptions in the handler are caught and the
    message is left pending — same effect.

    `stop_after` is a soft cap on processed messages, useful for tests
    and notebook demos. Real workers run with `stop_after=None`.

    Returns the number of messages processed.
    """
    await ensure_group(redis, group)
    processed = 0

    while stop_after is None or processed < stop_after:
        # `>` means "give me messages no one else in the group has seen"
        resp = await redis.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={STREAM: ">"},
            count=1,
            block=block_ms,
        )
        if not resp:
            # block_ms elapsed with no messages — check stop condition and loop
            continue

        # xreadgroup returns [(stream_name, [(msg_id, {field: value}), ...])]
        _, entries = resp[0]
        for msg_id, fields in entries:
            try:
                task = json.loads(fields[b"json"] if isinstance(list(fields.keys())[0], bytes) else fields["json"])
            except Exception as e:
                logger.exception("malformed message %s — acking to skip: %s", msg_id, e)
                await redis.xack(STREAM, group, msg_id)
                continue

            try:
                ok = await handler(task)
            except Exception:
                logger.exception("handler raised for task=%s", task.get("task_id"))
                ok = False

            if ok:
                await redis.xack(STREAM, group, msg_id)
                processed += 1
            else:
                logger.warning("not acking task=%s msg=%s — will redeliver", task.get("task_id"), msg_id)

    return processed
