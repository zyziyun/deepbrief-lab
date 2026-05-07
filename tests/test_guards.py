"""Tests for CostMeter and LoopGuard."""

from __future__ import annotations

import pytest

from deepbrief.agents.guards import (
    PRICING,
    BudgetExceeded,
    CostMeter,
    LoopGuard,
)


# ─────────────────────────────────────────────────────────────────────────────
# CostMeter
# ─────────────────────────────────────────────────────────────────────────────


class TestCostMeter:
    def test_charge_accumulates(self):
        m = CostMeter(budget_usd=10.0)
        c1 = m.charge("gpt-4o-mini", 1000, 100)
        c2 = m.charge("gpt-4o-mini", 1000, 100)
        assert c1 > 0
        assert m.spent_usd == pytest.approx(c1 + c2)
        assert len(m.calls) == 2

    def test_budget_exceeded_raises(self):
        m = CostMeter(budget_usd=0.0001)   # too small for one gpt-4o call
        with pytest.raises(BudgetExceeded):
            m.charge("gpt-4o", 10_000, 1_000)

    def test_unknown_model_does_not_crash(self):
        m = CostMeter(budget_usd=1.0)
        cost = m.charge("totally-fake-model", 1_000, 100)
        # Unknown model degrades to free — doesn't blow up the run
        assert cost == 0.0
        assert m.spent_usd == 0.0

    def test_exact_pricing_for_known_model(self):
        rate = PRICING["gpt-4o-mini"]
        m = CostMeter(budget_usd=1.0)
        cost = m.charge("gpt-4o-mini", 1_000_000, 0)
        assert cost == pytest.approx(rate["prompt"] * 1_000_000)


# ─────────────────────────────────────────────────────────────────────────────
# LoopGuard
# ─────────────────────────────────────────────────────────────────────────────


class TestLoopGuard:
    def test_fingerprint_is_stable(self):
        g = LoopGuard()
        fp1 = g.fingerprint("web_search", {"q": "x", "limit": 5})
        fp2 = g.fingerprint("web_search", {"limit": 5, "q": "x"})  # different key order
        assert fp1 == fp2

    def test_different_args_differ(self):
        g = LoopGuard()
        fp1 = g.fingerprint("web_search", {"q": "a"})
        fp2 = g.fingerprint("web_search", {"q": "b"})
        assert fp1 != fp2

    def test_threshold_trips(self):
        g = LoopGuard(repeat_threshold=3)
        assert not g.is_looping("t", {"x": 1})    # 1st
        assert not g.is_looping("t", {"x": 1})    # 2nd
        assert g.is_looping("t", {"x": 1})        # 3rd ← trip

    def test_distinct_calls_do_not_trip(self):
        g = LoopGuard(repeat_threshold=3)
        for i in range(10):
            looping = g.is_looping("t", {"x": i})
            assert not looping
