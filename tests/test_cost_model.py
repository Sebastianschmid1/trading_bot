"""Deterministische Tests fuer das Backtest-Kostenmodell."""

import pandas as pd
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


def test_default_cost_model_adds_conservative_slippage_to_legacy_spread():
    """Der Default ist bewusst NICHT mehr slippage-blind: auf die Legacy-Spread-Pauschale
    (2× legacy_pct je Round-Trip) kommt die konservative Slippage (Bruchteil des Spreads).
    Kosten werden dadurch eher über- als unterschätzt (Backtest-Ehrlichkeit)."""
    legacy_pct = 0.05
    frac = CostModel().slippage_spread_fraction
    assert frac > 0                                   # Nicht-Null-Default (nicht mehr geschönt)
    net_pct, pnl_eur = engine._net_pnl(100.0, 105.0, "long", 1000.0, 2.0, legacy_pct)

    cost_pct = 2 * legacy_pct * (1 + frac)            # Spread beidseitig + Slippage darauf
    assert net_pct == pytest.approx(5.0 - cost_pct)
    assert pnl_eur == pytest.approx(1000.0 * (5.0 - cost_pct) / 100.0 * 2.0)


def test_engine_trade_exposes_separate_costs_and_slippage():
    model = CostModel(commission_per_share=0.01, slippage_spread_fraction=0.5)
    net_pct, _pnl_eur, costs = engine._net_pnl_with_costs(
        100.0, 105.0, "long", 1000.0, 1.0, model,
        entry_volume=1_000_000.0, exit_volume=1_000_000.0,
    )

    assert costs.commission > 0.0
    assert costs.slippage == 0.0  # ohne Quote UND ohne Legacy-Spread keine Slippage-Basis
    assert net_pct < 5.0


def _one_fire_df():
    """Ein Gewinner-Long-Setup, das am zweiten Bar das TP (110) trifft (Volumen groß → voll fillbar)."""
    idx = pd.bdate_range("2020-01-01", periods=4)
    return pd.DataFrame({"Open": [100, 105, 110, 110], "High": [101, 111, 111, 111],
                         "Low": [99, 104, 109, 109], "Close": [100, 105, 110, 110],
                         "Volume": 1e7}, index=idx)


def test_default_costs_reach_walk_exit_results_and_penalize_pnl():
    """Part C: Der Nicht-Null-Default (inkl. Slippage) kommt über simulate_portfolio/_walk_exit
    im Ergebnis an — derselbe Trade ist mit Default-Kosten schlechter als kostenlos."""
    df = _one_fire_df()
    data = {"X": df}
    by_date = {str(df.index[0].date()): [{"ticker": "X", "idx": 0, "strength": 1.0,
                                          "direction": "long", "entry": 100.0,
                                          "sl": 95.0, "tp": 110.0}]}
    kw = dict(top_n=1, leverage=1.0, trade_size=1000.0, max_concurrent=1, max_hold=40)
    gross = engine.simulate_portfolio(data, by_date, cost_pct=0.0, **kw)
    default = engine.simulate_portfolio(data, by_date, **kw)   # cost_pct=None → Config-Default + Slippage

    assert gross[0]["reason"] == "Take-Profit" and gross[0]["exit"] == 110.0
    assert default[0]["pnl_eur"] < gross[0]["pnl_eur"]          # Kosten kommen an
    cb = default[0]["cost_breakdown"]
    assert cb["spread"] > 0.0 and cb["slippage"] > 0.0         # beides Nicht-Null im Default
