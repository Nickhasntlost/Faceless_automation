"""Unit tests for the budget guard (no network / no paid APIs)."""
from __future__ import annotations

import pytest

from src.models import PricingConfig
from src.module3_budget_guard import BudgetGuard
from src.utils.api_client import BudgetExceededError


def _pricing() -> PricingConfig:
    return PricingConfig(
        verified_at="test",
        script_model_id="m",
        script_input_usd_per_1m_tokens=1.5,
        script_output_usd_per_1m_tokens=9.0,
        veo_model_id="veo",
        veo_usd_per_second={"720p": 0.05, "1080p": 0.08},
        thumbnail_model_id="t",
        thumbnail_usd_per_1k_image=0.067,
        tts_usd_per_1m_characters=16.0,
    )


def _guard(tmp_path, threshold=10.0, credit=20.0) -> BudgetGuard:
    return BudgetGuard(
        store_path=tmp_path / "budget.json",
        threshold_usd=threshold,
        total_credit_usd=credit,
        pricing=_pricing(),
    )


def test_veo_cost_scales_with_resolution(tmp_path):
    g = _guard(tmp_path)
    assert g.estimate_veo_cost(6, "720p") == pytest.approx(0.30)
    assert g.estimate_veo_cost(6, "1080p") == pytest.approx(0.48)


def test_veo_cost_unknown_resolution_falls_back_to_720p(tmp_path):
    g = _guard(tmp_path)
    assert g.estimate_veo_cost(10, "4k") == pytest.approx(g.estimate_veo_cost(10, "720p"))


def test_record_spend_accumulates(tmp_path):
    g = _guard(tmp_path)
    assert g.cumulative_spend_usd == 0.0
    g.record_spend(1.0, "veo")
    g.record_spend(2.5, "tts")
    assert g.cumulative_spend_usd == pytest.approx(3.5)


def test_assert_can_spend_blocks_over_threshold(tmp_path):
    g = _guard(tmp_path, threshold=5.0, credit=100.0)
    g.record_spend(4.0, "veo")
    with pytest.raises(BudgetExceededError):
        g.assert_can_spend(2.0, "veo")  # 4 + 2 > 5


def test_assert_can_spend_allows_within_threshold(tmp_path):
    g = _guard(tmp_path, threshold=5.0)
    g.assert_can_spend(4.99, "veo")  # should not raise


def test_tts_cost_matches_rate(tmp_path):
    g = _guard(tmp_path)
    # 1,000,000 chars at $16/1M == $16
    assert g.estimate_tts_cost(1_000_000) == pytest.approx(16.0)
