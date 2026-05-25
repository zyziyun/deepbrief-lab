"""Editor — the human-in-the-loop approval gate.

Pattern: synchronous HITL on a Tier 2 action. The agent has produced a draft;
we pause until a human approves, edits, or rejects. **Durable state** —
approval requests are written to disk so they survive process restarts (a
production agent might be a worker that gets recycled mid-task).

The 4-tier HITL taxonomy:
  Tier 0 — Read-only, idempotent. Auto. (DB read, web search.)
  Tier 1 — Non-critical writes, low blast radius. Logged async. (file ticket.)
  Tier 2 — Irreversible writes, $ involved. **Sync HITL.** (Send email, write
           customer-facing content. Where the editor lives.)
  Tier 3 — Regulated / high-stakes. Block; multi-approver if needed.

We're a Tier 2 case: we're about to write Markdown to disk that has the user's
name on it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


Decision = Literal["approved", "rejected", "edited"]


@dataclass
class ApprovalRequest:
    request_id: str
    topic: str
    draft_markdown: str
    created_at: str
    state: Literal["pending", "approved", "rejected", "edited"] = "pending"
    decided_at: str | None = None
    decided_by: str | None = None
    final_markdown: str | None = None
    notes: str | None = None


class ApprovalStore:
    """File-backed approval queue. Each request is one JSON file in `pending/`,
    moved to `decided/` on resolution. Durable across restarts.
    """

    def __init__(self, base_dir: str | Path = "./data/approvals") -> None:
        self.base = Path(base_dir)
        (self.base / "pending").mkdir(parents=True, exist_ok=True)
        (self.base / "decided").mkdir(parents=True, exist_ok=True)

    def create(self, topic: str, draft_markdown: str) -> ApprovalRequest:
        req = ApprovalRequest(
            request_id=uuid.uuid4().hex[:12],
            topic=topic,
            draft_markdown=draft_markdown,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write(req, where="pending")
        return req

    def list_pending(self) -> list[ApprovalRequest]:
        out = []
        for p in (self.base / "pending").glob("*.json"):
            data = json.loads(p.read_text())
            out.append(ApprovalRequest(**data))
        return out

    def get(self, request_id: str) -> ApprovalRequest | None:
        for sub in ("pending", "decided"):
            p = self.base / sub / f"{request_id}.json"
            if p.exists():
                return ApprovalRequest(**json.loads(p.read_text()))
        return None

    def decide(
        self,
        request_id: str,
        decision: Decision,
        *,
        decided_by: str = "human",
        final_markdown: str | None = None,
        notes: str | None = None,
    ) -> ApprovalRequest:
        req = self.get(request_id)
        if not req:
            raise KeyError(f"Request {request_id} not found")
        req.state = decision
        req.decided_at = datetime.now(timezone.utc).isoformat()
        req.decided_by = decided_by
        req.final_markdown = final_markdown if decision == "edited" else (
            req.draft_markdown if decision == "approved" else None
        )
        req.notes = notes
        # Move file pending → decided
        old = self.base / "pending" / f"{req.request_id}.json"
        if old.exists():
            old.unlink()
        self._write(req, where="decided")
        return req

    def _write(self, req: ApprovalRequest, where: str) -> None:
        path = self.base / where / f"{req.request_id}.json"
        path.write_text(json.dumps(req.__dict__, indent=2))

    async def wait_for_decision(self, request_id: str, *, poll_s: float = 0.5, timeout_s: float = 600) -> ApprovalRequest:
        """Block until the request leaves 'pending'. Survives restarts."""
        elapsed = 0.0
        while elapsed < timeout_s:
            req = self.get(request_id)
            if req and req.state != "pending":
                return req
            await asyncio.sleep(poll_s)
            elapsed += poll_s
        raise TimeoutError(f"Request {request_id} not decided within {timeout_s}s")
