"""
Backtest-Engine (Tages-Timeframe, long-only, Demo).

Simuliert eine Strategie über historische Tageskurse — **ohne Look-ahead**: die Entscheidung
für Tag t nutzt nur Daten bis einschließlich t; Ausstiege werden ab t+1 geprüft.

Bewusste v1-Vereinfachungen (dokumentiert in docs/STRATEGIE_ROADMAP.md):
- nur Tages-Timeframe (yfinance liefert 5m/15m nur ~60 Tage; lange Historie nur als 1d).
- pro Ticker max. eine offene Position gleichzeitig (kein Pyramiding, keine Überlappung).
- Ausstieg über SL/TP (intrabar via High/Low) oder Zeitlimit `max_hold`.
- feste €-Tradegröße; Profitfaktor ist davon unabhängig (Verhältnis).
"""

import logging

import pandas as pd
import yfinance as yf

import evaluator
import metrics as metrics_mod
import strategies as strat_mod
import universes
from config import TRADE_SIZE_EUR, DEFAULT_REGION

log = logging.getLogger(__name__)

WARMUP_BARS = 260      # genug für EMA200 + ADX + Hüllkurven-Z-Scores (env_base=150)
MAX_HOLD_DAYS = 40     # ~2 Handelsmonate Obergrenze je Trade


def _download_daily(tickers: list[str], years: int) -> dict:
    """Lädt Tageskurse (ein Batch) und gibt {ticker: bereinigtes OHLCV-DataFrame} zurück."""
    period = f"{years + 1}y"   # +1 Jahr Warmup-Puffer vor dem Testfenster
    data = yf.download(tickers, period=period, interval="1d",
                       progress=False, auto_adjust=True, group_by="ticker")
    out = {}
    for t in tickers:
        try:
            df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
        except (KeyError, TypeError):
            continue
        df = df.dropna(subset=["Close"])
        if len(df) > WARMUP_BARS:
            out[t] = df
    return out


def backtest_ticker(strategy: strat_mod.Strategy, ticker: str, df: pd.DataFrame,
                    trade_size: float, max_hold: int = MAX_HOLD_DAYS,
                    warmup: int = WARMUP_BARS) -> list[dict]:
    """Simuliert eine Strategie für einen Ticker; gibt die Liste geschlossener Trades zurück."""
    trades = []
    n = len(df)
    i = warmup
    while i < n - 1:
        sig = strategy.generate(ticker, {"1d": df.iloc[:i + 1]})
        if not sig or sig.get("direction") != "long" or not sig.get("stop_loss"):
            i += 1
            continue

        entry = float(df["Close"].iloc[i])
        sl, tp = float(sig["stop_loss"]), float(sig["take_profit"])
        exit_price, reason, j = None, "Zeitlimit", i + 1
        while j < n and (j - i) <= max_hold:
            lo, hi = float(df["Low"].iloc[j]), float(df["High"].iloc[j])
            if lo <= sl:                         # konservativ: SL vor TP, falls beide am selben Tag
                exit_price, reason = sl, "Stop-Loss"
                break
            if hi >= tp:
                exit_price, reason = tp, "Take-Profit"
                break
            j += 1
        if exit_price is None:
            j = min(j, n - 1)
            exit_price, reason = float(df["Close"].iloc[j]), "Zeitlimit"

        pnl_pct, pnl_eur = evaluator.realized_pnl(entry, exit_price, "long", trade_size, 1.0)
        trades.append({
            "ticker": ticker, "entry": entry, "exit": exit_price,
            "pnl_pct": round(pnl_pct, 2), "pnl_eur": round(pnl_eur, 2),
            "reason": reason, "entry_date": str(df.index[i].date()),
            "exit_date": str(df.index[j].date()),
        })
        i = j + 1   # nächste Entscheidung erst nach dem Ausstieg (keine Überlappung)
    return trades


def run_backtest(strategy_key: str, tickers: list[str] | None = None, years: int = 2,
                 trade_size: float = TRADE_SIZE_EUR) -> dict:
    """Führt einen Backtest aus und gibt {strategy, metrics, trades, n_tickers, years} zurück.
    Trades chronologisch (nach Ausstiegsdatum) für eine saubere Equity-/Drawdown-Kurve."""
    strategy = strat_mod.get(strategy_key)
    if tickers is None:
        tickers = universes.get_tickers(DEFAULT_REGION, auto=False)   # kuratierter Korb

    data = _download_daily(tickers, years)
    all_trades = []
    for t, df in data.items():
        try:
            all_trades.extend(backtest_ticker(strategy, t, df, trade_size))
        except Exception as e:
            log.warning(f"Backtest {t} fehlgeschlagen: {e}")

    all_trades.sort(key=lambda x: x["exit_date"])
    return {
        "strategy":  strategy.key,
        "label":     strategy.label,
        "n_tickers": len(data),
        "years":     years,
        "metrics":   metrics_mod.compute_metrics(all_trades, initial_capital=trade_size * 10),
        "trades":    all_trades,
    }


def compare_strategies(keys: list[str], tickers: list[str] | None = None, years: int = 2,
                       trade_size: float = TRADE_SIZE_EUR) -> list[dict]:
    """Backtestet mehrere Strategien über denselben Korb/Zeitraum (sortiert nach Profitfaktor)."""
    results = [run_backtest(k, tickers=tickers, years=years, trade_size=trade_size) for k in keys]

    def pf_key(r):
        pf = r["metrics"]["profit_factor"]
        return pf if pf is not None else float("inf")   # „kein Verlust" ganz nach oben

    results.sort(key=pf_key, reverse=True)
    return results


# ── Portfolio-Backtest: top_n Signale/Tag + Hebel ────────────────────────────

def _walk_exit(df: pd.DataFrame, i: int, sl: float, tp: float, leverage: float,
               max_hold: int = MAX_HOLD_DAYS):
    """Ausstieg ab Bar i+1: Liquidation (Hebel) → SL → TP (intrabar via High/Low), sonst Zeitlimit."""
    n = len(df)
    liq = evaluator.liquidation_price(float(df["Close"].iloc[i]), leverage, "long")
    j = i + 1
    while j < n and (j - i) <= max_hold:
        lo, hi = float(df["Low"].iloc[j]), float(df["High"].iloc[j])
        if liq is not None and lo <= liq:
            return j, liq, "Liquidation"
        if lo <= sl:
            return j, sl, "Stop-Loss"
        if hi >= tp:
            return j, tp, "Take-Profit"
        j += 1
    j = min(j, n - 1)
    return j, float(df["Close"].iloc[j]), "Zeitlimit"


def gather_fires(strategy, data: dict, start_after) -> dict:
    """Teurer Teil (einmal pro Strategie): je Handelstag die feuernden Signale sammeln.
    Gibt {date_str: [ {ticker, idx, strength, entry, sl, tp}, ... ]} zurück."""
    by_date: dict[str, list] = {}
    for tkr, df in data.items():
        for i in range(WARMUP_BARS, len(df) - 1):
            if df.index[i] < start_after:
                continue
            sig = strategy.generate(tkr, {"1d": df.iloc[:i + 1]})
            if sig and sig.get("direction") == "long" and sig.get("stop_loss"):
                by_date.setdefault(str(df.index[i].date()), []).append({
                    "ticker": tkr, "idx": i, "strength": sig.get("strength", 0.0),
                    "entry": float(df["Close"].iloc[i]),
                    "sl": float(sig["stop_loss"]), "tp": float(sig["take_profit"]),
                })
    return by_date


def simulate_portfolio(data: dict, by_date: dict, top_n: int, leverage: float,
                       trade_size: float, max_concurrent: int,
                       max_hold: int = MAX_HOLD_DAYS) -> list[dict]:
    """Billiger Teil: aus den Feuer-Events die Trades simulieren (Hold = max_hold Tage).
    `max_hold=1` ≈ „am Tagesende schließen", großes max_hold = „halten bis SL/TP"."""
    trades = []
    open_pos: dict[str, pd.Timestamp] = {}            # ticker -> Ausstiegsdatum (blockiert Re-Entry)
    for d in sorted(by_date):
        d_ts = pd.Timestamp(d)
        open_pos = {t: ex for t, ex in open_pos.items() if ex > d_ts}
        free = max_concurrent - len(open_pos)
        if free <= 0:
            continue
        cands = sorted((s for s in by_date[d] if s["ticker"] not in open_pos),
                       key=lambda s: s["strength"], reverse=True)
        for s in cands[:min(top_n, free)]:
            df = data[s["ticker"]]
            ej, ex_price, reason = _walk_exit(df, s["idx"], s["sl"], s["tp"], leverage, max_hold)
            pnl_pct, pnl_eur = evaluator.realized_pnl(s["entry"], ex_price, "long", trade_size, leverage)
            open_pos[s["ticker"]] = df.index[ej]
            trades.append({
                "ticker": s["ticker"], "entry_date": d, "exit_date": str(df.index[ej].date()),
                "entry": s["entry"], "exit": ex_price, "pnl_pct": round(pnl_pct, 2),
                "pnl_eur": round(pnl_eur, 2), "reason": reason, "hold_days": (ej - s["idx"]),
            })
    trades.sort(key=lambda x: x["exit_date"])
    return trades


def signals_per_day(by_date: dict) -> dict:
    """Statistik, wie viele Signale eine Strategie pro Handelstag liefert."""
    counts = [len(v) for v in by_date.values()]
    if not counts:
        return {"avg": 0.0, "median": 0, "max": 0, "days": 0}
    counts.sort()
    return {"avg": round(sum(counts) / len(counts), 1), "median": counts[len(counts) // 2],
            "max": counts[-1], "days": len(counts)}


def backtest_portfolio(strategy_key: str, tickers: list[str] | None = None, years: int = 2,
                       top_n: int = 10, leverage: float = 5.0,
                       trade_size: float | None = None, initial_capital: float = 10000.0,
                       max_concurrent: int | None = None, max_hold: int = MAX_HOLD_DAYS) -> dict:
    """
    Portfolio-Simulation: pro Handelstag die besten `top_n` Signale (für noch nicht offene Ticker)
    eröffnen — mit `leverage`. Ausstieg via Liquidation/SL/TP/Zeitlimit (`max_hold`). Kein Look-ahead.

    Voll investiert: `max_concurrent` (Default top_n) begrenzt gleichzeitige Positionen,
    `trade_size` (Default initial_capital/top_n) ist die Margin je Position.
    `max_hold=1` ≈ „am Tagesende schließen".
    """
    strategy = strat_mod.get(strategy_key)
    if tickers is None:
        tickers = universes.get_tickers(DEFAULT_REGION, auto=False)
    if max_concurrent is None:
        max_concurrent = top_n
    if trade_size is None:
        trade_size = initial_capital / max(top_n, 1)

    data = _download_daily(tickers, years)
    if not data:
        return {"trades": [], "metrics": metrics_mod.compute_metrics([], initial_capital)}

    last = max(df.index[-1] for df in data.values())
    start_after = last - pd.Timedelta(days=int(years * 365.25))
    by_date = gather_fires(strategy, data, start_after)
    trades = simulate_portfolio(data, by_date, top_n, leverage, trade_size, max_concurrent, max_hold)

    return {
        "strategy": strategy.key, "label": strategy.label,
        "trades": trades, "metrics": metrics_mod.compute_metrics(trades, initial_capital),
        "leverage": leverage, "top_n": top_n, "years": years, "max_hold": max_hold,
        "n_tickers": len(data), "trade_size": trade_size, "initial_capital": initial_capital,
        "liquidations": sum(1 for t in trades if t["reason"] == "Liquidation"),
        "signals_per_day": signals_per_day(by_date),
        "start": str(start_after.date()), "end": str(last.date()),
    }
