"""
Backtest-Sweep-Reports für die Website (Reiter „Reports").

Erzeugt zwei JSON-Reports unter data/reports/:
  - strategies.json : Overall-View — je Strategie eine Kennzahlen-Zeile (native SL/TP, Hebel 1).
  - matrix.json     : VOLLE Matrix aus Strategie × SL/TP-Modus × Hebel (7×3×5 = 105 Analysen),
                      je mit Trades, P&L, Rendite, Trefferquote, Ø Haltedauer und Liquidationen.

Effizient: lädt den Korb EINMAL und berechnet die Signale EINMAL je Strategie (der teure Teil);
alle 105 Analysen werden aus denselben „Fires" abgeleitet (SL/TP bzw. Hebel nur in der Simulation variiert).

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
TRADE_SIZE = 1000.0          # Einsatz (Margin) pro Position (USD)
PORTFOLIO_TOPN = 10          # max. gleichzeitige Positionen
START_CAPITAL = TRADE_SIZE * PORTFOLIO_TOPN   # = 10.000 USD fester Kapitalpool


def _slim(m: dict) -> dict:
    """Kompakte, anzeigefertige Kennzahlen-Teilmenge inkl. Kapital-/Rendite-Kennzahlen.
    Fester Startkapital-Pool (10.000 USD); pro Position 1.000 USD Margin, max. 10 gleichzeitig
    (neue Signale werden übersprungen, wenn das Budget belegt ist — siehe simulate_portfolio).
    Endkapital = Startkapital + P&L; Rendite % = P&L / Startkapital × 100."""
    pnl = m.get("total_pnl_eur", 0.0)
    return {
        "trades":           m.get("trades", 0),
        "win_rate":         m.get("win_rate"),
        "profit_factor":    m.get("profit_factor"),     # None = kein Verlust (∞)
        "total_pnl_eur":    pnl,
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "expectancy":       m.get("expectancy"),
        "invested_capital": START_CAPITAL,
        "end_capital":      START_CAPITAL + pnl,
        "return_pct":       pnl / START_CAPITAL * 100,
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


def _simulate(data: dict, by_date: dict, *, top_n: int, leverage: float,
              trade_size: float, max_concurrent: int,
              trail_mode: str | None = None, trail_mult: float | None = None) -> list:
    return engine.simulate_portfolio(data, by_date, top_n=top_n, leverage=leverage,
                                     trade_size=trade_size, max_concurrent=max_concurrent,
                                     max_hold=engine.MAX_HOLD_DAYS,
                                     trail_mode=trail_mode, trail_mult=trail_mult)


def _metrics(trades: list, initial_capital: float) -> dict:
    m = _slim(metrics_mod.compute_metrics(trades, initial_capital=initial_capital))
    holds = [t["hold_days"] for t in trades if t.get("hold_days") is not None]
    m["avg_hold_days"] = round(sum(holds) / len(holds), 1) if holds else None
    m["liquidations"] = sum(1 for t in trades if t.get("reason") == "Liquidation")
    return m


def _sim_metrics(data: dict, by_date: dict, *, top_n: int, leverage: float,
                 trade_size: float, initial_capital: float, max_concurrent: int,
                 trail_mode: str | None = None, trail_mult: float | None = None) -> dict:
    return _metrics(_simulate(data, by_date, top_n=top_n, leverage=leverage,
                              trade_size=trade_size, max_concurrent=max_concurrent,
                              trail_mode=trail_mode, trail_mult=trail_mult), initial_capital)


def _downsample(pts: list, max_points: int = 140) -> list:
    """Dünnt eine Punktliste gleichmäßig auf max_points aus (Endpunkt bleibt erhalten)."""
    if len(pts) <= max_points:
        return pts
    idx = sorted(set(int(i * (len(pts) - 1) / (max_points - 1)) for i in range(max_points)))
    return [pts[i] for i in idx]


def _equity_curve(trades: list, start_capital: float, start_date: str, end_date: str) -> list:
    """Realisierte Depotentwicklung (Start + kumulierter P&L), gestuft je Trade-Ausstieg.
    Gibt [[datum, depotwert], ...] zurück (downgesampelt)."""
    by_date = {}
    cum = 0.0
    for t in sorted(trades, key=lambda x: x["exit_date"]):
        cum += t["pnl_eur"]
        by_date[t["exit_date"]] = round(start_capital + cum, 2)
    pts = [[start_date, start_capital]] + [[d, by_date[d]] for d in sorted(by_date)]
    if end_date and pts[-1][0] != end_date:
        pts.append([end_date, pts[-1][1]])
    return _downsample(pts)


def _sp500_benchmark(years: int, start_capital: float, start_date: str, end_date: str):
    """Buy-&-Hold-S&P-500-Referenz (SPY, inkl. Dividenden via auto_adjust) über das Testfenster.
    Gibt (kennzahlen_zeile, equity_kurve) zurück oder (None, []) bei Fehler/zu wenig Daten."""
    try:
        sdf = engine._download_daily(["SPY"], years).get("SPY")
        if sdf is None:
            return None, []
        sdf = sdf.loc[start_date:end_date]
        closes = sdf["Close"].astype(float)
        if len(closes) < 3:
            return None, []
        c0, cN = float(closes.iloc[0]), float(closes.iloc[-1])
        eq = closes / c0 * start_capital
        curve = _downsample([[str(d.date()), round(float(v), 2)] for d, v in eq.items()])
        dd = float((eq / eq.cummax() - 1.0).min()) * 100
        pnl = start_capital * (cN / c0 - 1.0)
        row = {"key": "buyhold_sp500", "label": "Buy & Hold S&P 500", "trades": 1,
               "win_rate": 100.0 if pnl >= 0 else 0.0,
               "profit_factor": None if pnl >= 0 else 0.0,
               "total_pnl_eur": pnl, "max_drawdown_pct": abs(round(dd, 2)),
               "expectancy": pnl, "invested_capital": start_capital,
               "end_capital": start_capital + pnl, "return_pct": (cN / c0 - 1.0) * 100,
               "avg_hold_days": len(closes), "liquidations": 0}
        return row, curve
    except Exception as e:
        print(f"  ! Benchmark (S&P 500) übersprungen: {e}", flush=True)
        return None, []


def collect(region: str, years: int, limit: int | None, full: bool = True, jobs: int = 1) -> dict:
    # Standard: vollständiges Universum (komplette S&P 500, ~500 Werte). --curated = kleiner Korb (schnell).
    tickers = universes.get_tickers(region, auto=full)
    if limit:
        tickers = tickers[:limit]
    print(f"→ Lade {len(tickers)} Ticker ({years} J{', voll' if full else ', kuratiert'}) ...", flush=True)
    data = engine._download_daily(tickers, years)
    print(f"→ {len(data)} Ticker mit ausreichender Historie.", flush=True)

    strat_rows, matrix_rows, equity_curves = [], [], {}
    keys = [s.key for s in strat_mod.all_strategies()]
    print(f"→ Signale berechnen ({jobs} Prozess{'e' if jobs != 1 else ''}, {len(data)} Ticker × {len(keys)} Strategien) ...", flush=True)
    fires_by_strat = _gather_all(data, keys, jobs)

    last = max(df.index[-1] for df in data.values())
    start_str = str((last - pd.Timedelta(days=int(years * 365.25))).date())
    end_str = str(last.date())

    print("→ S&P-500-Benchmark (Buy & Hold) laden ...", flush=True)
    bench_row, bench_curve = _sp500_benchmark(years, START_CAPITAL, start_str, end_str)

    for s in strat_mod.all_strategies():
        by_date = fires_by_strat[s.key]
        print(f"  · {s.key}: {sum(len(v) for v in by_date.values())} Signale → Reports ableiten ...", flush=True)

        trail_mult = strat_mod.strategy_runtime_params(s.key).get("trail_mult") if s.key == "ai_adaptive" else None
        trail_mode = "atr" if trail_mult else None
        # Fester 10.000-USD-Pool: max. 10 Positionen à 1.000 USD; ist das Budget belegt,
        # werden neue Signale übersprungen (simulate_portfolio: free = max_concurrent − offene).
        # Overall-View: native SL/TP, Hebel 1.
        strat_rows.append({"key": s.key, "label": s.label,
                           **_sim_metrics(data, by_date, top_n=PORTFOLIO_TOPN, leverage=1.0,
                                          trade_size=TRADE_SIZE, initial_capital=START_CAPITAL,
                                          max_concurrent=PORTFOLIO_TOPN,
                                          trail_mode=trail_mode, trail_mult=trail_mult)})

        # Vollständige Matrix: jede Kombination aus SL/TP-Modus × Hebel (gleicher 10k-Pool).
        # Pro Kombination zusätzlich die Depot-Equity-Kurve (für den Klick-auf-Zeile-Chart).
        by_mode = {mode: _with_mode(by_date, mode) for mode in MODES}
        for mode in MODES:
            for lev in LEVERAGES:
                trades = _simulate(data, by_mode[mode], top_n=PORTFOLIO_TOPN, leverage=float(lev),
                                   trade_size=TRADE_SIZE, max_concurrent=PORTFOLIO_TOPN,
                                   trail_mode=trail_mode, trail_mult=trail_mult)
                matrix_rows.append({"key": s.key, "label": s.label, "mode": mode, "leverage": lev,
                                    **_metrics(trades, START_CAPITAL)})
                equity_curves[f"{s.key}|{lev}|{mode}"] = _equity_curve(trades, START_CAPITAL,
                                                                       start_str, end_str)

    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "region": region, "years": years, "n_tickers": len(data),
        "trade_size_usd": TRADE_SIZE, "portfolio_top_n": PORTFOLIO_TOPN,
        "start_capital_usd": START_CAPITAL,
        "universe": "voll" if full else "kuratiert",
    }
    if bench_row:
        strat_rows.append(bench_row)

    return {
        "strategies": {**meta, "rows": strat_rows},
        "matrix":     {**meta, "modes": MODES, "leverages": LEVERAGES,
                       "strategies": [{"key": s.key, "label": s.label} for s in strat_mod.all_strategies()],
                       "rows": matrix_rows},
        "equity":     {"start_capital": START_CAPITAL, "start": start_str, "end": end_str,
                       "curves": equity_curves, "benchmark": bench_curve},
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
        path = out_dir / f"{name}_{args.years}y.json"   # je Zeitraum eigene Datei (1/3/5/8/15y)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        count = len(payload["rows"]) if "rows" in payload else len(payload.get("curves", {}))
        print(f"✓ {path}  ({count} Einträge)")
    print(f"Fertig in {time.time() - t0:.0f}s ({jobs} Prozesse) → {out_dir}")


if __name__ == "__main__":
    main()
