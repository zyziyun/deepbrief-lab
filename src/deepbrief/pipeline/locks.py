"""IdempotencyLock — the Redis SETNX pattern from S9 §5.2.

Queues at-least-once-deliver. If a worker crashes after running the agent
but before acking, the task gets redelivered. Without idempotency, you run
the agent twice — pay 2× tokens, possibly double-write side effects.

The pattern: **SETNX** (SET if Not eXists) — atomic claim semantics.

The release uses a Lua script for **atomic check-and-delete**. Without this,
if the lock TTL expires between your GET and DEL, you might delete a
*different* worker's lock. Mention the Lua script in interviews to show
you've shipped this.
"""

from __future__ import annotations

import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


class IdempotencyLock:
    """Redis-backed lock keyed by task_id. Atomic claim + atomic release."""

    def __init__(self, redis: Redis, ttl_seconds: int = 600) -> None:
        self.redis = redis
        # TTL must be longer than the worst-case agent runtime — otherwise the
        # lock expires while we're still working and a redelivery would acquire it.
        self.ttl = ttl_seconds

    def _key(self, task_id: str) -> str:
        return f"agent:lock:{task_id}"

    async def acquire(self, task_id: str, worker_id: str) -> bool:
        """Returns True iff this worker won the lock.

        Uses SET key value NX EX ttl — atomic in Redis. NX means "only set
        if the key doesn't already exist." Returns None (falsy) if another
        worker already holds the lock.
        """
        result = await self.redis.set(self._key(task_id), worker_id, nx=True, ex=self.ttl)
        won = result is True
        if won:
            logger.debug("lock acquired: task=%s worker=%s", task_id, worker_id)
        return won

    async def release(self, task_id: str, worker_id: str) -> bool:
        """Only release if WE still own it.

        Uses a Lua script so the GET and DEL are atomic — without this,
        another worker could have already taken over after our TTL expired
        and we'd be deleting their lock.

        Returns True if we deleted the lock, False if someone else owned it
        (or it had already expired).
        """
        result = await self.redis.eval(_RELEASE_SCRIPT, 1, self._key(task_id), worker_id)
        deleted = bool(result)
        if deleted:
            logger.debug("lock released: task=%s worker=%s", task_id, worker_id)
        return deleted
