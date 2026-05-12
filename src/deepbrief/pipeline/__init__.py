"""Pipeline — producer / queue / worker for running the LangGraph brief as a service.

Components map line-by-line to **S9 Part 5**:

- `locks.py`      → §5.2 — Redis SETNX idempotency lock with Lua release
- `progress.py`   → §5.3 — Pub/Sub progress emission + SSE subscriber
- `streams.py`    → §5.1 — Redis Streams producer + consumer wrappers
- `store.py`      → §5.4 step 5 — durable result store (SQLite for the lab)
- `worker.py`     → §5.4 — the six-step consume → check → lock → run → emit → store → ack loop
- `api.py`        → §5.1 producer — FastAPI POST /runs (enqueue) + GET /runs/{id}/stream (SSE)
"""
