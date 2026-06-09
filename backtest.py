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
