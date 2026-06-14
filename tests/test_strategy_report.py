"""
Tests für tools/strategy_report.py — nur der reine, netz-freie Teil:
Markdown-Aufbau (build_markdown), Lose-Rate, Profitfaktor-Anzeige und Sharpe-Sortierung.

Lauf:  python -m tests.test_strategy_report   oder   pytest tests/test_strategy_report.py
"""

import sys

from tools import strategy_report as report


def _trade(trades=10, wins=6, losses=3, pf=1.5, win_rate=60.0, payoff=2.0,
           t_stat=2.5, total_pnl=50.0):
    return {"trades": trades, "wins": wins, "losses": losses, "win_rate": win_rate,
            "profit_factor": pf, "total_pnl_eur": total_pnl, "expectancy": 0.5,
            "max_drawdown_pct": 5.0, "avg_win": 2.0, "avg_loss": -1.0,
            "payoff_ratio": payoff, "avg_trade_pct": 0.5, "best_trade_pct": 8.0,
            "worst_trade_pct": -4.0, "max_consec_wins": 4, "max_consec_losses": 2,
            "kelly_pct": 30.0, "t_stat": t_stat}


def _equity(sharpe=1.0, cagr=12.0):
    return {"total_return_pct": 25.0, "cagr_pct": cagr, "ann_vol_pct": 14.0,
            "sharpe": sharpe, "sortino": (None if sharpe is None else sharpe + 0.3),
            "calmar": (None if sharpe is None else 1.8), "max_dd_pct": 8.0,
            "max_dd_days": 30, "recovery_factor": 3.0, "beta": 0.9,
            "alpha_pct": 4.0, "corr": 0.8}


def _extra():
    return {"exposure": 60.0, "avg_concurrent": 3.2, "trades_per_year": 5.0,
            "avg_hold": 9.0, "spd": {"avg": 4.0, "median": 4, "max": 12, "days": 100}}


def _data():
    return {
        "config": {"region": "sp500", "region_label": "S&P 500", "years": 2, "top_n": 10,
                   "leverage": 1.0, "n_tickers": 40, "start": "2024-06-14", "end": "2026-06-14",
                   "generated": "2026-06-14", "init_cap": 10000.0,
                   "benchmark": {"cagr_pct": 17.0, "sharpe": 0.95, "sortino": 1.1,
                                 "calmar": 0.9, "max_dd_pct": 18.9, "ann_vol_pct": 16.0}},
        "rows": [
            {"key": "mid", "label": "Mittel-Strat", "description": "Beschreibung M.",
             "trade": _trade(t_stat=1.0), "equity": _equity(sharpe=0.5), "extra": _extra()},
            {"key": "best", "label": "Top-Strat", "description": "Beste.",
             "trade": _trade(t_stat=3.0), "equity": _equity(sharpe=2.0), "extra": _extra()},
            {"key": "none", "label": "Ohne-Sharpe", "description": "Flach.",
             "trade": _trade(t_stat=0.5), "equity": _equity(sharpe=None), "extra": _extra()},
        ],
    }


# ── Anzeige-Helfer ─────────────────────────────────────────────────────────────

def test_lose_rate_and_break_even():
    assert report._lose_rate({"trades": 0, "losses": 0}) == 0.0
    assert report._lose_rate({"trades": 10, "losses": 3}) == 30.0
    assert report._break_even({"trades": 10, "wins": 6, "losses": 3}) == 1


def test_pf_str_variants():
    assert report._pf_str({"trades": 5, "profit_factor": None}) == "∞"
    assert report._pf_str({"trades": 0, "profit_factor": None}) == "—"
    assert report._pf_str({"trades": 9, "profit_factor": 1.53}) == "1.53"


def test_num_none_safe():
    assert report._num(None) == "—"
    assert report._num(1.234) == "1.23"
    assert report._num(None, "{:+.1f}") == "—"


# ── build_markdown ─────────────────────────────────────────────────────────────

def test_ranking_sorted_by_sharpe_none_last():
    md = report.build_markdown(_data(), charts=True)
    i_best = md.index("Top-Strat")
    i_mid = md.index("Mittel-Strat")
    i_none = md.index("Ohne-Sharpe")
    assert i_best < i_mid < i_none          # Sharpe 2.0 > 0.5 > None


def test_methodik_and_benchmark_and_significance():
    md = report.build_markdown(_data(), charts=True)
    assert "Methodik" in md and "Sharpe" in md
    assert "S&P 500 (Buy & Hold)" in md     # Benchmark-Zeile in der Rangliste
    # t-Statistik-Signifikanzmarkierung: best (t=3) signifikant, none (t=0.5) schwach
    assert "✅ signifikant" in md and "⚠️ schwach" in md
    # Metadaten je Strategie
    for key, desc in [("best", "Beste."), ("mid", "Beschreibung M."), ("none", "Flach.")]:
        assert f"`{key}`" in md and desc in md


def test_charts_toggle():
    with_c = report.build_markdown(_data(), charts=True)
    without = report.build_markdown(_data(), charts=False)
    assert "backtest_strategien_vergleich.png" in with_c
    assert "backtest_strategien_vergleich.png" not in without


def test_handles_missing_benchmark_and_none_metrics():
    d = _data()
    d["config"]["benchmark"] = None         # kein Benchmark verfügbar
    md = report.build_markdown(d, charts=False)
    assert "S&P 500 (Buy & Hold)" not in md
    assert "Ohne-Sharpe" in md              # None-Sharpe-Zeile rendert trotzdem (—)


# ── Runner (ohne pytest nutzbar) ─────────────────────────────────────────────

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
