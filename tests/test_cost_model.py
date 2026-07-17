"""Deterministische Tests fuer das Backtest-Kostenmodell."""

import pytest

from stockbot.backtest.cost_model import CostModel
from stockbot.backtest import engine


def test_cost_breakdown_separates_all_components_and_sums_total():
    model = CostModel(
        commission_per_share=0.01,
        slippage_spread_fraction=0.5,
        sec_fee_rate=0.001,
        finra_taf_per_share=0.02,
        finra_taf_cap=0.15,
        liquidity_penalty_pct=1.0,
        market_impact_coefficient=0.01,
    )
    costs = model.calculate_trade(
        100.0, 110.0, "long", 1000.0, entry_volume=100.0, exit_volume=100.0,
        entry_bid=99.5, entry_ask=100.5, exit_bid=109.5, exit_ask=110.5,
    )

    assert costs.commission == pytest.approx(0.20)
    assert costs.spread == pytest.approx(10.0)
    assert costs.slippage == pytest.approx(5.0)
    assert costs.sec_fee == pytest.approx(1.0)       # nur der Long-Ausstieg ist Verkauf
    assert costs.finra_taf == pytest.approx(0.15)    # gedeckelt
    assert costs.liquidity_penalty == pytest.approx(2.0)
    assert costs.market_impact == pytest.approx(2 * 1000.0 * 0.01 * 0.1 ** 0.5)
    assert costs.as_dict()["total"] == pytest.approx(costs.total)


def test_partial_fill_limits_costs_to_fillable_daily_volume():
    model = CostModel(commission_per_share=1.0, max_volume_fraction=0.1)
    costs = model.calculate_trade(100.0, 100.0, "long", 1000.0,
                                  entry_volume=50.0, exit_volume=80.0)

    assert costs.requested_shares == pytest.approx(10.0)
    assert costs.filled_shares == pytest.approx(5.0)
    assert costs.unfilled_shares == pytest.approx(5.0)
    assert costs.commission == pytest.approx(10.0)  # zwei Seiten mal fuenf gefuellte Aktien


def test_default_cost_model_is_equivalent_to_legacy_cost_pct():
    legacy_pct = 0.05
    net_pct, pnl_eur = engine._net_pnl(100.0, 105.0, "long", 1000.0, 2.0, legacy_pct)

    assert net_pct == pytest.approx(5.0 - 2 * legacy_pct)
    assert pnl_eur == pytest.approx(1000.0 * (5.0 - 2 * legacy_pct) / 100.0 * 2.0)


def test_engine_trade_exposes_separate_costs_and_slippage():
    model = CostModel(commission_per_share=0.01, slippage_spread_fraction=0.5)
    net_pct, _pnl_eur, costs = engine._net_pnl_with_costs(
        100.0, 105.0, "long", 1000.0, 1.0, model,
        entry_volume=1_000_000.0, exit_volume=1_000_000.0,
    )

    assert costs.commission > 0.0
    assert costs.slippage == 0.0  # ohne Quote keine behauptete Slippage-Annahme
    assert net_pct < 5.0
