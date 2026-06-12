#!/usr/bin/env python3
"""
Strategie-Suche / Backtest-Vergleich (Tages-Timeframe, long-only, ATR-SL/TP, lokale Engine).

Vergleicht ALLE aktuellen Strategien (stockbot.market.strategies.REGISTRY) plus optionale
Kandidaten auf EXAKT denselben Kursdaten (ein einziger Download) → fairer 2-Jahres-(o. ä.)-Vergleich.
Sortiert nach Gesamt-P&L; zusätzlich Profitfaktor, Trefferquote, Erwartungswert/Trade, Max-Drawdown.

⚠️  Lädt Kursdaten von yfinance und rechnet je nach Korb/Jahren einige Minuten — das ist der
    rechen-/zeitintensive Teil. Vorher mit `--limit` an wenigen Tickern testen.

Lauf (vom Repo-Root):
    python tools/strategy_search.py                          # S&P-Korb, 2 Jahre
    python tools/strategy_search.py --region msci_world --years 3
    python tools/strategy_search.py --limit 8                # Schnelltest auf 8 Tickern
    python tools/strategy_search.py --region emerging --years 2 --trade-size 25

Eigene Idee testen: unten eine generate-Funktion ergänzen und in CANDIDATES eintragen.
(Die Gewinner der letzten Suche — high52 / high52_wide — sind bereits in der Registry.)
"""

import os
import sys
import argparse
from types import SimpleNamespace

import numpy as np

# Repo-Root auf den Importpfad setzen, damit `python tools/strategy_search.py` funktioniert
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stockbot.market import universes, strategies, analyzer   # noqa: E402
from stockbot.backtest import engine                          # noqa: E402
from stockbot.core import metrics as M                        # noqa: E402
from stockbot.config import RSI_PERIOD                        # noqa: E402

_clean = strategies._clean_1d
_mk = strategies._make_signal


# ── Beispiel-Kandidaten (Vorlage zum Erweitern) ──────────────────────────────
# Jede Funktion: (ticker, tf_data) -> Signal-Dict | None, wie die Registry-Strategien.
# _mk(ticker, df, key, strength, sl_mult, tp_mult) baut Signal + ATR-SL/TP + Anzeige-Felder.

def donchian55(ticker, tf):
    """Längerer Breakout (Turtle-artig): über das 55-Tage-Hoch + Trendfilter >MA100."""
    df = _clean(tf)
    if df is None or len(df) < 210:
        return None
    c = df["Close"].astype(float).values
    h = df["High"].astype(float).values
    if not (c[-1] >= float(np.max(h[-56:-1])) and c[-1] > float(np.mean(c[-100:]))):
        return None
    return _mk(ticker, df, "donchian55", 75.0, sl_mult=2.0, tp_mult=4.0)


def roc_mom(ticker, tf):
    """Absolutes Momentum: 6-Monats-ROC > 15 % im Aufwärtstrend (Kurs>MA50>MA200)."""
    df = _clean(tf)
    if df is None or len(df) < 210:
        return None
    c = df["Close"].astype(float).values
    roc = (c[-1] / c[-127] - 1.0) * 100.0
    ma50 = float(np.mean(c[-50:])); ma200 = float(np.mean(c[-200:]))
    if not (roc > 15.0 and c[-1] > ma50 > ma200):
        return None
    return _mk(ticker, df, "roc_mom", 76.0, sl_mult=2.5, tp_mult=5.0)


def rsi2_dip(ticker, tf):
    """Mean-Reversion (Connors-artig): RSI(2) < 10 im langfristigen Aufwärtstrend (Kurs>MA200)."""
    df = _clean(tf)
    if df is None or len(df) < 210:
        return None
    c = df["Close"].astype(float).values
    rsi2 = analyzer.calc_rsi(c, 2)
    if not (rsi2 < 10 and c[-1] > float(np.mean(c[-200:]))):
        return None
    strength = 60 + 40 * max(0.0, min(1.0, (10 - rsi2) / 10))
    return _mk(ticker, df, "rsi2_dip", strength, sl_mult=2.5, tp_mult=2.0)


CANDIDATES = {
    "donchian55": donchian55,
    "roc_mom": roc_mom,
    "rsi2_dip": rsi2_dip,
    # "meine_idee": meine_idee,
}


# ── Backtest-Vergleich ───────────────────────────────────────────────────────

def _bt(strat, data, trade_size, init_cap):
    trades = []
    for t, df in data.items():
        try:
            trades += engine.backtest_ticker(strat, t, df, trade_size)
        except Exception:
            pass
    return M.compute_metrics(trades, init_cap)


def run(region="sp500", years=2, limit=None, trade_size=25.0):
    init_cap = trade_size * 10
    tickers = universes.get_tickers(region, auto=False)
    if limit:
        tickers = tickers[:limit]
    print(f"Region {region}: {len(tickers)} Ticker, {years}J — lade Kursdaten …", flush=True)
    data = engine._download_daily(tickers, years)
    print(f"Verwertbare Ticker: {len(data)}\n", flush=True)

    rows = []
    for strat in strategies.all_strategies():
        rows.append((strat.label, "AKTUELL", _bt(strat, data, trade_size, init_cap)))
        print(f"  fertig (aktuell): {strat.label}", flush=True)
    for key, fn in CANDIDATES.items():
        strat = SimpleNamespace(key=key, label=key, generate=fn)
        rows.append((key, "KANDIDAT", _bt(strat, data, trade_size, init_cap)))
        print(f"  fertig (kandidat): {key}", flush=True)

    rows.sort(key=lambda r: r[2]["total_pnl_eur"], reverse=True)
    print(f"\n{'Strategie':30} {'Typ':8} {'PF':>6} {'Win%':>6} {'Trades':>7} "
          f"{'P&L€':>9} {'Exp€':>7} {'MaxDD%':>7}")
    print("-" * 86)
    for label, typ, m in rows:
        pf = m["profit_factor"]
        pfs = "inf" if pf is None and m["trades"] else (f"{pf:.2f}" if pf is not None else "-")
        print(f"{label[:30]:30} {typ:8} {pfs:>6} {m['win_rate']:>6} {m['trades']:>7} "
              f"{m['total_pnl_eur']:>9.0f} {m['expectancy']:>7.2f} {m['max_drawdown_pct']:>7.1f}")


def main():
    ap = argparse.ArgumentParser(description="Strategie-Backtest-Vergleich (aktuelle + Kandidaten).")
    ap.add_argument("--region", default="sp500", help="Korb: sp500 | msci_world | emerging")
    ap.add_argument("--years", type=int, default=2, help="Testfenster in Jahren (Default 2)")
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten N Ticker (Schnelltest)")
    ap.add_argument("--trade-size", type=float, default=25.0, help="€-Tradegröße je Position")
    a = ap.parse_args()
    run(region=a.region, years=a.years, limit=a.limit, trade_size=a.trade_size)


if __name__ == "__main__":
    main()
