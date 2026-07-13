"""Tests for the pure, mode-isolated RES-002 report calculation."""

import pytest

from stockbot.core.domain import Fill, Mode, PerformanceSnapshot, Position
from stockbot.core.mode_report import build_mode_report


def _fill(mode=Mode.PAPER, *, fee=0.0):
    return Fill(
        id=None, order_id=1, qty=1.0, price=100.0,
        filled_at="2026-07-13T10:00:00Z", mode=mode, fee=fee,
    )


def _position(mode=Mode.PAPER):
    return Position(
        id=None, user_id=1, ticker="AAPL", strategy_version_id=8, mode=mode,
    )


def _snapshot(pnl, *, mode=Mode.PAPER, strategy_version_id=7, open_risk=None):
    return PerformanceSnapshot(
        id=None, strategy_version_id=strategy_version_id, mode=mode,
        captured_at="2026-07-13T11:00:00Z", net_pnl=pnl, open_risk=open_risk,
    )


@pytest.mark.parametrize(
    ("keyword", "foreign"),
    [
        ("fills", [_fill(Mode.BACKTEST)]),
        ("positions", [_position(Mode.BACKTEST)]),
        ("performance_snapshots", [_snapshot(1.0, mode=Mode.BACKTEST)]),
    ],
)
def test_report_rejects_mixed_modes(keyword, foreign):
    with pytest.raises(ValueError, match="does not match report mode"):
        build_mode_report(Mode.LIVE, **{keyword: foreign})


def test_hand_calculated_metrics_are_correct():
    report = build_mode_report(
        Mode.PAPER,
        fills=[_fill(fee=1.0), _fill(fee=0.5)],
        positions=[_position()],
        performance_snapshots=[
            _snapshot(20.0, open_risk=12.0),
            _snapshot(-10.0),
            _snapshot(5.0, strategy_version_id=9),
            _snapshot(-5.0, open_risk=7.0),
        ],
    )

    assert report.net_pnl == 10.0
    assert report.costs == 1.5
    assert report.drawdown == 10.0
    assert report.trade_count == 4
    assert report.profit_factor == pytest.approx(25 / 15)
    assert report.expectancy == 2.5
    assert report.open_risk == 7.0
    assert report.strategy_versions == [7, 8, 9]


def test_unmodelled_values_are_none_instead_of_estimated():
    report = build_mode_report(Mode.PAPER, fills=[_fill(fee=2.0)], positions=[_position()])

    assert report.costs == 2.0
    assert report.net_pnl is None
    assert report.slippage is None
    assert report.drawdown is None
    assert report.trade_count is None
    assert report.profit_factor is None
    assert report.expectancy is None
    assert report.open_risk is None
