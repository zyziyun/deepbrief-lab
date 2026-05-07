"""Tests for ApprovalStore — the durable HITL approval queue."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from deepbrief.agents.editor import ApprovalStore


@pytest.fixture
def store(tmp_path: Path) -> ApprovalStore:
    return ApprovalStore(base_dir=tmp_path / "approvals")


class TestApprovalStore:
    def test_create_writes_pending_file(self, store: ApprovalStore):
        req = store.create(topic="x", draft_markdown="# Draft")
        assert req.state == "pending"
        path = Path(store.base) / "pending" / f"{req.request_id}.json"
        assert path.exists()
        on_disk = json.loads(path.read_text())
        assert on_disk["topic"] == "x"
        assert on_disk["state"] == "pending"

    def test_get_finds_pending_and_decided(self, store: ApprovalStore):
        req = store.create(topic="x", draft_markdown="# d")
        assert store.get(req.request_id).state == "pending"
        store.decide(req.request_id, "approved")
        assert store.get(req.request_id).state == "approved"

    def test_decide_approved_keeps_draft(self, store: ApprovalStore):
        req = store.create(topic="x", draft_markdown="# original")
        decided = store.decide(req.request_id, "approved", decided_by="alice")
        assert decided.state == "approved"
        assert decided.decided_by == "alice"
        assert decided.final_markdown == "# original"

    def test_decide_edited_uses_new_markdown(self, store: ApprovalStore):
        req = store.create(topic="x", draft_markdown="# original")
        decided = store.decide(req.request_id, "edited", final_markdown="# fixed")
        assert decided.state == "edited"
        assert decided.final_markdown == "# fixed"

    def test_decide_rejected_has_no_final(self, store: ApprovalStore):
        req = store.create(topic="x", draft_markdown="# d")
        decided = store.decide(req.request_id, "rejected", notes="off-topic")
        assert decided.state == "rejected"
        assert decided.final_markdown is None
        assert decided.notes == "off-topic"

    def test_decide_moves_file_pending_to_decided(self, store: ApprovalStore):
        req = store.create(topic="x", draft_markdown="# d")
        pending = Path(store.base) / "pending" / f"{req.request_id}.json"
        decided = Path(store.base) / "decided" / f"{req.request_id}.json"
        assert pending.exists() and not decided.exists()

        store.decide(req.request_id, "approved")
        assert not pending.exists()
        assert decided.exists()

    def test_decide_unknown_id_raises(self, store: ApprovalStore):
        with pytest.raises(KeyError):
            store.decide("does-not-exist", "approved")

    def test_list_pending_excludes_decided(self, store: ApprovalStore):
        a = store.create(topic="a", draft_markdown="# a")
        b = store.create(topic="b", draft_markdown="# b")
        store.decide(a.request_id, "approved")
        pending = store.list_pending()
        ids = {r.request_id for r in pending}
        assert b.request_id in ids
        assert a.request_id not in ids

    @pytest.mark.asyncio
    async def test_wait_for_decision_returns_immediately_if_decided(
        self, store: ApprovalStore
    ):
        req = store.create(topic="x", draft_markdown="# d")
        store.decide(req.request_id, "approved")
        # Already decided → should return without sleeping
        decision = await asyncio.wait_for(
            store.wait_for_decision(req.request_id, poll_s=0.05),
            timeout=2.0,
        )
        assert decision.state == "approved"

    @pytest.mark.asyncio
    async def test_wait_for_decision_picks_up_async_decision(
        self, store: ApprovalStore
    ):
        req = store.create(topic="x", draft_markdown="# d")

        async def decide_after_delay() -> None:
            await asyncio.sleep(0.2)
            store.decide(req.request_id, "approved", decided_by="bot")

        # Run wait + delayed-decide concurrently
        decision, _ = await asyncio.gather(
            store.wait_for_decision(req.request_id, poll_s=0.05, timeout_s=5.0),
            decide_after_delay(),
        )
        assert decision.state == "approved"
        assert decision.decided_by == "bot"

    @pytest.mark.asyncio
    async def test_wait_for_decision_times_out(self, store: ApprovalStore):
        req = store.create(topic="x", draft_markdown="# d")
        with pytest.raises(TimeoutError):
            await store.wait_for_decision(req.request_id, poll_s=0.05, timeout_s=0.2)
