"""Mode-getrennte Dashboard-Reports (W3.4 → Gate P6 / RES-002)."""

from stockbot.core import mode_reporting
from stockbot.core.domain import Mode, PerformanceSnapshot


def _trade(pnl, sv_id=7, ticker="AAPL", trade_date="2026-07-01"):
    return {"id": 1, "ticker": ticker, "trade_date": trade_date, "pnl_eur": pnl,
            "signal": {"strategy": "standard", "strategy_version_id": sv_id}}


def test_paper_report_aggregates_closed_trades():
    trades = [_trade(10.0), _trade(-4.0), _trade(6.0, sv_id=8)]
    report = mode_reporting.paper_report(trades)
    assert report.mode is Mode.PAPER
    assert report.net_pnl == 12.0
    assert report.trade_count == 3
    assert report.profit_factor == 16.0 / 4.0
    assert report.strategy_versions == [7, 8]


def test_paper_report_ignores_trades_without_pnl():
    report = mode_reporting.paper_report([_trade(None), _trade(5.0)])
    assert report.trade_count == 1 and report.net_pnl == 5.0


def test_shadow_report_is_empty_without_data_and_never_mixes_paper():
    report = mode_reporting.shadow_report()
    assert report.mode is Mode.SHADOW
    assert report.net_pnl is None and report.trade_count is None

    # Ein Shadow-Snapshot fließt in den Shadow-Report — Paper bleibt davon unberührt.
    snap = PerformanceSnapshot(id=None, strategy_version_id=3, mode=Mode.SHADOW,
                               captured_at="2026-07-01", net_pnl=9.0)
    shadow = mode_reporting.shadow_report([snap])
    assert shadow.net_pnl == 9.0
    paper = mode_reporting.paper_report([_trade(1.0)])
    assert paper.net_pnl == 1.0   # keine Vermischung


def test_build_mode_reports_returns_serializable_paper_and_shadow():
    reports = mode_reporting.build_mode_reports([_trade(3.0)])
    assert set(reports) == {"paper", "shadow"}
    assert reports["paper"]["mode"] == "paper"       # String, kein Enum
    assert reports["shadow"]["mode"] == "shadow"
    assert reports["paper"]["net_pnl"] == 3.0
    assert reports["shadow"]["net_pnl"] is None
