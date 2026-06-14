"""
Backtest-Sweep-Reports für die Website (Reiter „Reports").

Erzeugt drei JSON-Reports unter data/reports/:
  - strategies.json : je Strategie eine Kennzahlen-Zeile (native SL/TP, Hebel 1).
  - sltp.json       : je Strategie × SL/TP-Modus (passiv/normal/aggressiv).
  - leverage.json   : je Strategie × Hebel (1/2/3/5/10), Portfolio-Simulation.

Effizient: lädt den Korb EINMAL und berechnet die Signale EINMAL je Strategie (der teure Teil);
alle Reports werden aus denselben „Fires" abgeleitet (SL/TP bzw. Hebel nur in der Simulation variiert).

Lauf (im Repo-Root, venv aktiv):
  python -m tools.sweep_report                 # kuratierter S&P-Korb, 2 Jahre
  python -m tools.sweep_report --years 3 --limit 15
"""

import os
import sys
import json
import time
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

from stockbot.paths import REPORTS_DIR
from stockbot.market import strategies as strat_mod
from stockbot.market import universes
from stockbot.backtest import engine
from stockbot.core import metrics as metrics_mod
from stockbot.config import SL_TP_MODES, DEFAULT_REGION

MODES = ["passiv", "normal", "aggressiv"]
LEVERAGES = [1, 2, 3, 5, 10]
TRADE_SIZE = 1000.0          # fester Einsatz pro Trade (USD) — einheitlich für ALLE Reports
PORTFOLIO_TOPN = 10


def _slim(m: dict) -> dict:
    """Kompakte, anzeigefertige Kennzahlen-Teilmenge inkl. Kapital-/Rendite-Kennzahlen.
    Eingesetztes Kapital = Anzahl Trades × Einsatz; Endkapital = eingesetzt + P&L;
    Rendite % = P&L / eingesetztes Kapital × 100."""
    trades = m.get("trades", 0)
    pnl = m.get("total_pnl_eur", 0.0)
    invested = trades * TRADE_SIZE
    return {
        "trades":           trades,
        "win_rate":         m.get("win_rate"),
        "profit_factor":    m.get("profit_factor"),     # None = kein Verlust (∞)
        "total_pnl_eur":    pnl,
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "expectancy":       m.get("expectancy"),
        "invested_capital": invested,
        "end_capital":      invested + pnl,
        "return_pct":       (pnl / invested * 100) if invested else None,
    }


def _gather_ticker(args) -> dict:
    """Worker (in eigenem Prozess): berechnet für EINEN Ticker die „Fires" ALLER Strategien.
    Jeder Ticker ist unabhängig → so wird die teure Signalberechnung über alle CPU-Kerne verteilt.
    Speichert je Fire zusätzlich ATR + native SL/TP (für die SL/TP-Modus-Ableitung ohne Neuberechnung).
    Gibt {strategy_key: [fire,...]} zurück (jede Fire trägt ihr Datum)."""
    ticker, df, strat_keys = args
    n = len(df)
    out: dict[str, list] = {k: [] for k in strat_keys}
    for sk in strat_keys:
        gen = strat_mod.get(sk).generate
        fires = out[sk]
        for i in range(engine.WARMUP_BARS, n - 1):
            sig = gen(ticker, {"1d": df.iloc[:i + 1]})
            if not sig or sig.get("direction") != "long" or not sig.get("stop_loss"):
                continue
            fires.append({
                "date": str(df.index[i].date()), "ticker": ticker, "idx": i,
                "strength": sig.get("strength", 0.0), "entry": float(df["Close"].iloc[i]),
                "atr": sig.get("atr"), "sl": float(sig["stop_loss"]), "tp": float(sig["take_profit"]),
            })
    return out


def _gather_all(data: dict, strat_keys: list, jobs: int) -> dict:
    """Berechnet die Fires aller Ticker × Strategien — parallel über `jobs` Prozesse.
    Gibt {strategy_key: {date: [fire,...]}} zurück."""
    tasks = [(t, df, strat_keys) for t, df in data.items()]
    fires_by_strat = {k: {} for k in strat_keys}

    def _merge(per_ticker: dict):
        for sk, fires in per_ticker.items():
            bd = fires_by_strat[sk]
            for f in fires:
                bd.setdefault(f["date"], []).append(f)

    if jobs <= 1:
        for res in map(_gather_ticker, tasks):
            _merge(res)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            for res in ex.map(_gather_ticker, tasks, chunksize=2):
                _merge(res)
    return fires_by_strat


def _with_mode(by_date: dict, mode: str) -> dict:
    """Kopie der Fires mit per ATR neu gesetzten SL/TP gemäß Modus (Fires ohne ATR entfallen)."""
    sl_mult, tp_mult = SL_TP_MODES.get(mode, (None, None))
    out: dict[str, list] = {}
    for d, fires in by_date.items():
        new = []
        for f in fires:
            atr = f.get("atr")
            if not atr or atr <= 0 or sl_mult is None:
                continue
            g = dict(f)
            g["sl"] = f["entry"] - sl_mult * atr
            g["tp"] = f["entry"] + tp_mult * atr
            new.append(g)
        if new:
            out[d] = new
    return out


def _sim_metrics(data: dict, by_date: dict, *, top_n: int, leverage: float,
                 trade_size: float, initial_capital: float, max_concurrent: int) -> dict:
    trades = engine.simulate_portfolio(data, by_date, top_n=top_n, leverage=leverage,
                                       trade_size=trade_size, max_concurrent=max_concurrent,
                                       max_hold=engine.MAX_HOLD_DAYS)
    return _slim(metrics_mod.compute_metrics(trades, initial_capital=initial_capital))


def collect(region: str, years: int, limit: int | None, full: bool = True, jobs: int = 1) -> dict:
    # Standard: vollständiges Universum (komplette S&P 500, ~500 Werte). --curated = kleiner Korb (schnell).
    tickers = universes.get_tickers(region, auto=full)
    if limit:
        tickers = tickers[:limit]
    print(f"→ Lade {len(tickers)} Ticker ({years} J{', voll' if full else ', kuratiert'}) ...", flush=True)
    data = engine._download_daily(tickers, years)
    print(f"→ {len(data)} Ticker mit ausreichender Historie.", flush=True)

    strat_rows, sltp_rows, lev_rows = [], [], []
    big = max(50, len(data))            # „alle Signale" (Einzelposition je Ticker, kein Top-N-Schnitt)

    keys = [s.key for s in strat_mod.all_strategies()]
    print(f"→ Signale berechnen ({jobs} Prozess{'e' if jobs != 1 else ''}, {len(data)} Ticker × {len(keys)} Strategien) ...", flush=True)
    fires_by_strat = _gather_all(data, keys, jobs)

    for s in strat_mod.all_strategies():
        by_date = fires_by_strat[s.key]
        print(f"  · {s.key}: {sum(len(v) for v in by_date.values())} Signale → Reports ableiten ...", flush=True)

        # einheitlicher Einsatz 1.000 USD/Trade in ALLEN Reports.
        # Strategie-Report: native SL/TP, Hebel 1, alle Signale.
        strat_rows.append({"key": s.key, "label": s.label,
                           **_sim_metrics(data, by_date, top_n=big, leverage=1.0,
                                          trade_size=TRADE_SIZE, initial_capital=TRADE_SIZE * 10,
                                          max_concurrent=big)})

        # SL/TP-Report: je Modus (per ATR), Hebel 1.
        by_mode = {}
        for mode in MODES:
            bm = _with_mode(by_date, mode)
            by_mode[mode] = _sim_metrics(data, bm, top_n=big, leverage=1.0,
                                         trade_size=TRADE_SIZE, initial_capital=TRADE_SIZE * 10,
                                         max_concurrent=big)
        sltp_rows.append({"key": s.key, "label": s.label, "by_mode": by_mode})

        # Hebel-Report: native SL/TP, Portfolio (Top-N/Tag), je Hebel.
        by_lev = {}
        for lev in LEVERAGES:
            by_lev[str(lev)] = _sim_metrics(data, by_date, top_n=PORTFOLIO_TOPN, leverage=float(lev),
                                            trade_size=TRADE_SIZE,
                                            initial_capital=TRADE_SIZE * PORTFOLIO_TOPN,
                                            max_concurrent=PORTFOLIO_TOPN)
        lev_rows.append({"key": s.key, "label": s.label, "by_lev": by_lev})

    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "region": region, "years": years, "n_tickers": len(data),
        "trade_size_usd": TRADE_SIZE, "portfolio_top_n": PORTFOLIO_TOPN,
        "universe": "voll" if full else "kuratiert",
    }
    return {
        "strategies": {**meta, "rows": strat_rows},
        "sltp":       {**meta, "modes": MODES, "rows": sltp_rows},
        "leverage":   {**meta, "leverages": LEVERAGES, "rows": lev_rows},
    }


def main():
    ap = argparse.ArgumentParser(description="Backtest-Sweep-Reports erzeugen (Strategie/SL-TP/Hebel).")
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten N Ticker (Schnelltest)")
    ap.add_argument("--curated", action="store_true", help="kleiner kuratierter Korb statt vollem Universum")
    ap.add_argument("--jobs", type=int, default=0,
                    help="parallele Prozesse (0 = alle CPU-Kerne, 1 = seriell)")
    ap.add_argument("--out", default=None, help="Ziel-Verzeichnis (Default: data/reports)")
    args = ap.parse_args()

    jobs = args.jobs if args.jobs > 0 else (os.cpu_count() or 1)
    out_dir = Path(args.out) if args.out else REPORTS_DIR
    t0 = time.time()
    reports = collect(args.region, args.years, args.limit, full=not args.curated, jobs=jobs)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in reports.items():
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✓ {path}  ({len(payload['rows'])} Zeilen)")
    print(f"Fertig in {time.time() - t0:.0f}s ({jobs} Prozesse) → {out_dir}")


if __name__ == "__main__":
    main()
