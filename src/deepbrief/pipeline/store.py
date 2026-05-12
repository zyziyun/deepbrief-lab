"""ResultStore — durable result persistence from S9 §5.4 step 5.

The lecture uses MongoDB (`mongo.runs.insert_one(...)`). For the lab we
use SQLite because it's zero-dep and writes survive process restart, which
is the only property that matters for the worker idempotency story.

API mirrors the lecture: `find_one(task_id)`, `insert_one(record)`.
Replacement with Motor/Mongo or asyncpg/Postgres is a one-class change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    task_id      TEXT PRIMARY KEY,
    query        TEXT NOT NULL,
    answer       TEXT,
    state        TEXT,        -- full state as JSON
    completed_at TEXT NOT NULL
);
"""


class ResultStore:
    """SQLite-backed result store. Drop-in for MongoDB in S9 §5 patterns."""

    def __init__(self, path: str | Path = "./data/runs.db") -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # Use check_same_thread=False so the worker's asyncio loop can hand
        # off connections between threads if it ever needs to.
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.executescript(_SCHEMA)
        self._lock = asyncio.Lock()       # sqlite3 isn't async; serialize writes

    async def find_one(self, task_id: str) -> dict | None:
        async with self._lock:
            cur = self._conn.execute("SELECT * FROM runs WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        rec = dict(zip(cols, row))
        if rec.get("state"):
            try:
                rec["state"] = json.loads(rec["state"])
            except json.JSONDecodeError:
                pass
        return rec

    async def insert_one(self, *, task_id: str, query: str, answer: str | None, state: Any) -> None:
        """Insert (or replace) one run.

        `state` can be the full agent state — anything JSON-serializable.
        Stored as a JSON blob; later you can query / migrate it however.
        """
        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs (task_id, query, answer, state, completed_at) VALUES (?, ?, ?, ?, ?)",
                (
                    task_id,
                    query,
                    answer,
                    json.dumps(state, default=str) if state is not None else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def close(self) -> None:
        self._conn.close()
