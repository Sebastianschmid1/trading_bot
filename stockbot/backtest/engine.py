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

from stockbot.core import evaluator
from stockbot.core import metrics as metrics_mod
from stockbot.market import strategies as strat_mod
from stockbot.market import analyzer
from stockbot.market import universes
from stockbot.config import TRADE_SIZE_EUR, DEFAULT_REGION

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


def _generate(strategy: strat_mod.Strategy, ticker: str, tf_data: dict, allow_short: bool):
    """Signal-Erzeugung für den Backtest. Mit `allow_short` (nur Backtest) liefert die
    Standard-Strategie zusätzlich Short-Signale (über `analyzer.analyze_ticker`); alle anderen
    Strategien sind long-only und ignorieren das Flag."""
    if allow_short and strategy.key == "standard":
        sig = analyzer.analyze_ticker(ticker, tf_data, allow_short=True)
        if sig is not None:
            sig.setdefault("strategy", "standard")
        return sig
    return strategy.generate(ticker, tf_data)


def backtest_ticker(strategy: strat_mod.Strategy, ticker: str, df: pd.DataFrame,
                    trade_size: float, max_hold: int = MAX_HOLD_DAYS,
                    warmup: int = WARMUP_BARS, sl_tp_mode: str | None = None,
                    allow_short: bool = False) -> list[dict]:
    """Simuliert eine Strategie für einen Ticker; gibt die Liste geschlossener Trades zurück.
    `sl_tp_mode` (passiv/normal/aggressiv) überschreibt – falls gesetzt – die SL/TP der Strategie
    einheitlich per ATR (für die Modus-Vergleichs-Reports).
    `allow_short` (nur Backtest) erlaubt zusätzlich Short-Trades (gespiegeltes SL/TP)."""
    trades = []
    n = len(df)
    i = warmup
    while i < n - 1:
        sig = _generate(strategy, ticker, {"1d": df.iloc[:i + 1]}, allow_short)
        if sl_tp_mode:
            sig = analyzer.apply_sl_tp_mode(sig, sl_tp_mode)
        if not sig or sig.get("direction") not in ("long", "short") or not sig.get("stop_loss"):
            i += 1
            continue

        direction = sig["direction"]
        entry = float(df["Close"].iloc[i])
        sl, tp = float(sig["stop_loss"]), float(sig["take_profit"])
        exit_price, reason, j = None, "Zeitlimit", i + 1
        while j < n and (j - i) <= max_hold:
            lo, hi = float(df["Low"].iloc[j]), float(df["High"].iloc[j])
            if direction == "long":
                if lo <= sl:                     # konservativ: SL vor TP, falls beide am selben Tag
                    exit_price, reason = sl, "Stop-Loss"; break
                if hi >= tp:
                    exit_price, reason = tp, "Take-Profit"; break
            else:                                # short: SL liegt ÜBER, TP UNTER dem Einstieg
                if hi >= sl:
                    exit_price, reason = sl, "Stop-Loss"; break
                if lo <= tp:
                    exit_price, reason = tp, "Take-Profit"; break
            j += 1
        if exit_price is None:
            j = min(j, n - 1)
            exit_price, reason = float(df["Close"].iloc[j]), "Zeitlimit"

        pnl_pct, pnl_eur = evaluator.realized_pnl(entry, exit_price, direction, trade_size, 1.0)
        trades.append({
            "ticker": ticker, "direction": direction, "entry": entry, "exit": exit_price,
            "pnl_pct": round(pnl_pct, 2), "pnl_eur": round(pnl_eur, 2),
            "reason": reason, "entry_date": str(df.index[i].date()),
            "exit_date": str(df.index[j].date()),
        })
        i = j + 1   # nächste Entscheidung erst nach dem Ausstieg (keine Überlappung)
    return trades


def _direction_split(trades: list[dict]) -> dict:
    """Zählt Long/Short und das jeweilige Gesamt-P&L (zeigt, ob Shorts etwas beitragen)."""
    out = {"n_long": 0, "n_short": 0, "pnl_long": 0.0, "pnl_short": 0.0}
    for t in trades:
        if t.get("direction") == "short":
            out["n_short"] += 1
            out["pnl_short"] += t.get("pnl_eur") or 0.0
        else:
            out["n_long"] += 1
            out["pnl_long"] += t.get("pnl_eur") or 0.0
    out["pnl_long"] = round(out["pnl_long"], 2)
    out["pnl_short"] = round(out["pnl_short"], 2)
    return out


def run_backtest(strategy_key: str, tickers: list[str] | None = None, years: int = 2,
                 trade_size: float = TRADE_SIZE_EUR, allow_short: bool = False) -> dict:
    """Führt einen Backtest aus und gibt {strategy, metrics, trades, n_tickers, years} zurück.
    Trades chronologisch (nach Ausstiegsdatum) für eine saubere Equity-/Drawdown-Kurve.
    `allow_short` (nur Standard-Strategie) testet zusätzlich Short-Setups."""
    strategy = strat_mod.get(strategy_key)
    if tickers is None:
        tickers = universes.get_tickers(DEFAULT_REGION, auto=False)   # kuratierter Korb

    data = _download_daily(tickers, years)
    all_trades = []
    for t, df in data.items():
        try:
            all_trades.extend(backtest_ticker(strategy, t, df, trade_size, allow_short=allow_short))
        except Exception as e:
            log.warning(f"Backtest {t} fehlgeschlagen: {e}")

    all_trades.sort(key=lambda x: x["exit_date"])
    return {
        "strategy":  strategy.key,
        "label":     strategy.label,
        "n_tickers": len(data),
        "years":     years,
        "allow_short": allow_short,
        "direction_split": _direction_split(all_trades),
        "metrics":   metrics_mod.compute_metrics(all_trades, initial_capital=trade_size * 10),
        "trades":    all_trades,
    }


def compare_strategies(keys: list[str], tickers: list[str] | None = None, years: int = 2,
                       trade_size: float = TRADE_SIZE_EUR, allow_short: bool = False) -> list[dict]:
    """Backtestet mehrere Strategien über denselben Korb/Zeitraum (sortiert nach Profitfaktor)."""
    results = [run_backtest(k, tickers=tickers, years=years, trade_size=trade_size,
                            allow_short=allow_short) for k in keys]

    def pf_key(r):
        pf = r["metrics"]["profit_factor"]
        return pf if pf is not None else float("inf")   # „kein Verlust" ganz nach oben

    results.sort(key=pf_key, reverse=True)
    return results


# ── Portfolio-Backtest: top_n Signale/Tag + Hebel ────────────────────────────

def _walk_exit(df: pd.DataFrame, i: int, sl: float, tp: float, leverage: float,
               max_hold: int = MAX_HOLD_DAYS, direction: str = "long"):
    """Ausstieg ab Bar i+1: Liquidation (Hebel) → SL → TP (intrabar via High/Low), sonst Zeitlimit.
    Bei `direction="short"` ist alles gespiegelt: SL liegt ÜBER, TP UNTER dem Einstieg, und
    liquidiert wird bei steigendem Kurs (Hoch ≥ Liquidationskurs)."""
    n = len(df)
    liq = evaluator.liquidation_price(float(df["Close"].iloc[i]), leverage, direction)
    j = i + 1
    while j < n and (j - i) <= max_hold:
        lo, hi = float(df["Low"].iloc[j]), float(df["High"].iloc[j])
        if direction == "long":
            if liq is not None and lo <= liq:
                return j, liq, "Liquidation"
            if lo <= sl:
                return j, sl, "Stop-Loss"
            if hi >= tp:
                return j, tp, "Take-Profit"
        else:                                # short: Liquidation/SL oben, TP unten
            if liq is not None and hi >= liq:
                return j, liq, "Liquidation"
            if hi >= sl:
                return j, sl, "Stop-Loss"
            if lo <= tp:
                return j, tp, "Take-Profit"
        j += 1
    j = min(j, n - 1)
    return j, float(df["Close"].iloc[j]), "Zeitlimit"


def gather_fires(strategy, data: dict, start_after, allow_short: bool = False) -> dict:
    """Teurer Teil (einmal pro Strategie): je Handelstag die feuernden Signale sammeln.
    Gibt {date_str: [ {ticker, idx, strength, direction, entry, sl, tp}, ... ]} zurück.
    `allow_short` (nur Standard-Strategie) sammelt zusätzlich Short-Setups."""
    by_date: dict[str, list] = {}
    for tkr, df in data.items():
        for i in range(WARMUP_BARS, len(df) - 1):
            if df.index[i] < start_after:
                continue
            sig = _generate(strategy, tkr, {"1d": df.iloc[:i + 1]}, allow_short)
            if sig and sig.get("direction") in ("long", "short") and sig.get("stop_loss"):
                by_date.setdefault(str(df.index[i].date()), []).append({
                    "ticker": tkr, "idx": i, "strength": sig.get("strength", 0.0),
                    "direction": sig["direction"],
                    "entry": float(df["Close"].iloc[i]),
                    "sl": float(sig["stop_loss"]), "tp": float(sig["take_profit"]),
                })
    return by_date


def simulate_portfolio(data: dict, by_date: dict, top_n: int, leverage: float,
                       trade_size: float, max_concurrent: int,
                       max_hold: int = MAX_HOLD_DAYS) -> list[dict]:
    """Billiger Teil: aus den Feuer-Events die Trades simulieren (Hold = max_hold Tage).
    `max_hold=1` ≈ „am Tagesende schließen", großes max_hold = „halten bis SL/TP".
    Die Richtung (long/short) wird je Feuer-Event übernommen (Default long)."""
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
            direction = s.get("direction", "long")
            ej, ex_price, reason = _walk_exit(df, s["idx"], s["sl"], s["tp"], leverage,
                                              max_hold, direction)
            pnl_pct, pnl_eur = evaluator.realized_pnl(s["entry"], ex_price, direction,
                                                      trade_size, leverage)
            open_pos[s["ticker"]] = df.index[ej]
            trades.append({
                "ticker": s["ticker"], "entry_date": d, "exit_date": str(df.index[ej].date()),
                "entry": s["entry"], "exit": ex_price, "pnl_pct": round(pnl_pct, 2),
                "pnl_eur": round(pnl_eur, 2), "reason": reason, "hold_days": (ej - s["idx"]),
                "direction": direction,
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
                       max_concurrent: int | None = None, max_hold: int = MAX_HOLD_DAYS,
                       allow_short: bool = False) -> dict:
    """
    Portfolio-Simulation: pro Handelstag die besten `top_n` Signale (für noch nicht offene Ticker)
    eröffnen — mit `leverage`. Ausstieg via Liquidation/SL/TP/Zeitlimit (`max_hold`). Kein Look-ahead.

    Voll investiert: `max_concurrent` (Default top_n) begrenzt gleichzeitige Positionen,
    `trade_size` (Default initial_capital/top_n) ist die Margin je Position.
    `max_hold=1` ≈ „am Tagesende schließen".
    `allow_short` (nur Standard-Strategie, Backtest) nimmt zusätzlich Short-Setups auf.
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
    by_date = gather_fires(strategy, data, start_after, allow_short=allow_short)
    trades = simulate_portfolio(data, by_date, top_n, leverage, trade_size, max_concurrent, max_hold)

    return {
        "strategy": strategy.key, "label": strategy.label,
        "trades": trades, "metrics": metrics_mod.compute_metrics(trades, initial_capital),
        "leverage": leverage, "top_n": top_n, "years": years, "max_hold": max_hold,
        "allow_short": allow_short, "direction_split": _direction_split(trades),
        "n_tickers": len(data), "trade_size": trade_size, "initial_capital": initial_capital,
        "liquidations": sum(1 for t in trades if t["reason"] == "Liquidation"),
        "signals_per_day": signals_per_day(by_date),
        "start": str(start_after.date()), "end": str(last.date()),
    }
