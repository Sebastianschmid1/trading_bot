"""
Backtest-Engine (Tages-Timeframe, long-only, Demo).

Simuliert eine Strategie über historische Tageskurse — **ohne Look-ahead**: die Entscheidung
für Tag t nutzt nur Daten bis einschließlich t; Ausstiege werden ab t+1 geprüft.

Bewusste v1-Vereinfachungen (dokumentiert in docs/STRATEGIE_ROADMAP.md):
- nur Tages-Timeframe (yfinance liefert 5m/15m nur ~60 Tage; lange Historie nur als 1d).
- pro Ticker max. eine offene Position gleichzeitig (kein Pyramiding, keine Überlappung).
- Ausstieg über SL/TP (intrabar via High/Low) oder Zeitlimit `max_hold`.
- feste €-Tradegröße; Profitfaktor ist davon unabhängig (Verhältnis).

Es gibt bewusst kein Resampling: Der Backtest verarbeitet ausschließlich vom Provider gelieferte
1d-Bars. Deren Index (und eine etwaige Zeitzone) wird unverändert an den `BarClock` gereicht.
"""

import os
import logging
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from dataclasses import asdict as _dc_asdict

from stockbot.backtest.clock import BarClock, Clock
from stockbot.backtest.cost_model import CostBreakdown, CostModel
from stockbot.backtest.reproducibility import DEFAULT_SEED, capture_run_metadata, set_seed
from stockbot.core import evaluator
from stockbot.core import metrics as metrics_mod
from stockbot.core.market_data import MarketDataProvider
from stockbot.market import strategies as strat_mod
from stockbot.market import analyzer
from stockbot.market.data_providers import YFinanceResearchProvider
from stockbot.market import universes
from stockbot import config
from stockbot.config import TRADE_SIZE_EUR, DEFAULT_REGION, BACKTEST_JOBS

log = logging.getLogger(__name__)

WARMUP_BARS = 260      # genug für EMA200 + ADX + Hüllkurven-Z-Scores (env_base=150)
MAX_HOLD_DAYS = 40     # ~2 Handelsmonate Obergrenze je Trade
PARALLEL_MIN = 8       # ab so vielen Tickern lohnt der Prozess-Pool (darunter: seriell, kein Spawn)


def _resolve_cost(cost_pct: float | None) -> float:
    """Effektive Transaktionskosten in % je Seite: explizit > Config-Default."""
    return config.BACKTEST_COST_PCT if cost_pct is None else float(cost_pct)


def _resolve_cost_model(cost_pct: float | None, cost_model: CostModel | None) -> CostModel:
    """Explizites Modell hat Vorrang; sonst die bisherige Kostenpauschale nachbilden."""
    return cost_model or CostModel(legacy_cost_pct=_resolve_cost(cost_pct))


def _net_pnl_with_costs(entry: float, exit_price: float, direction: str, trade_size: float,
                        leverage: float, cost_model: CostModel,
                        entry_volume: float | None = None,
                        exit_volume: float | None = None) -> tuple[float, float, CostBreakdown]:
    """P&L nach Modellkosten; ungefuellte Restmenge nimmt nicht am Trade teil."""
    pnl_pct, _ = evaluator.realized_pnl(entry, exit_price, direction, trade_size, leverage)
    effective_leverage = leverage or 1.0
    position_notional = trade_size * effective_leverage
    breakdown = cost_model.calculate_trade(entry, exit_price, direction, position_notional,
                                           entry_volume=entry_volume, exit_volume=exit_volume)
    fill_ratio = breakdown.filled_shares / breakdown.requested_shares if breakdown.requested_shares else 0.0
    cost_pct = breakdown.total / position_notional * 100.0 if position_notional else 0.0
    net_pct = pnl_pct * fill_ratio - cost_pct
    pnl_eur = trade_size * (net_pct / 100.0) * effective_leverage
    return net_pct, max(pnl_eur, -trade_size), breakdown


def _net_pnl(entry: float, exit_price: float, direction: str, trade_size: float,
             leverage: float, cost_pct: float) -> tuple[float, float]:
    """P&L NACH Round-Trip-Transaktionskosten (`cost_pct` % des Positionswerts je Seite).
    Kosten wirken wie eine zusätzliche Kursbewegung gegen den Trade; der Verlust bleibt
    wie in evaluator.realized_pnl auf die Margin begrenzt."""
    net_pct, pnl_eur, _ = _net_pnl_with_costs(
        entry, exit_price, direction, trade_size, leverage,
        CostModel(legacy_cost_pct=cost_pct or 0.0))
    return net_pct, pnl_eur


# ── Parallelisierung (Ticker sind unabhängig → über Prozesse verteilen) ──────
#
# CPU-gebunden: pro Bar wird ein Signal erzeugt. Threads brächten wegen des GIL wenig,
# daher Prozesse (wie tools/sweep_report.py). Ergebnisse bleiben bitidentisch zum seriellen
# Lauf — nur die Ticker-Arbeit wird verteilt; Reihenfolge/Sortierung bleiben erhalten.

def _resolve_jobs(jobs: int | None) -> int:
    """Effektive Worker-Zahl: explizit > Config BACKTEST_JOBS > alle Kerne. Minimum 1."""
    j = jobs if jobs else BACKTEST_JOBS
    if not j:
        j = os.cpu_count() or 1
    return max(1, int(j))


def _pmap(fn, items: list, jobs: int | None) -> list:
    """Wendet `fn` auf jedes Element an — parallel über Prozesse, wenn es sich lohnt, sonst
    seriell. Reihenfolge bleibt erhalten (deterministisch, gleiche Ergebnisse wie seriell).
    Unter PARALLEL_MIN Elementen oder bei jobs≤1 wird kein Pool gestartet (kein Spawn-Overhead;
    hält auch Tests/Monkeypatches auf dem seriellen Pfad)."""
    j = _resolve_jobs(jobs)
    if j <= 1 or len(items) < PARALLEL_MIN:
        return [fn(it) for it in items]
    workers = min(j, len(items))
    chunk = max(1, len(items) // (workers * 4))
    # "spawn" erzwingen: Der Bot-Prozess ist multithreaded (Scheduler/Telegram/Dashboard) —
    # ein fork() daraus könnte geerbte Locks verklemmen. spawn ist thread-sicher und plattform-
    # gleich (Windows nutzt es ohnehin). Fällt bei Pool-Fehlern auf den seriellen Pfad zurück.
    try:
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
            return list(ex.map(fn, items, chunksize=chunk))
    except Exception as e:
        log.warning(f"Parallel-Backtest fehlgeschlagen ({e}) — seriell ausgeführt.")
        return [fn(it) for it in items]


def _bt_one(args):
    """Backtestet EINEN Ticker (Worker für den Prozess-Pool). Alle Argumente sind picklebar;
    die Strategie wird im Kindprozess aus ihrem Schlüssel rekonstruiert."""
    (strategy_key, ticker, df, trade_size, max_hold, warmup, sl_tp_mode, allow_short,
     trail_mode, trail_mult, cost_pct, cost_model, clock) = args
    try:
        return backtest_ticker(strat_mod.get(strategy_key), ticker, df, trade_size,
                               max_hold=max_hold, warmup=warmup, sl_tp_mode=sl_tp_mode,
                               allow_short=allow_short, trail_mode=trail_mode, trail_mult=trail_mult,
                               cost_pct=cost_pct, cost_model=cost_model, clock=clock)
    except Exception as e:
        log.warning(f"Backtest {ticker} fehlgeschlagen: {e}")
        return []


def _fires_one(args):
    """Sammelt die feuernden Signale EINES Tickers (Worker, Portfolio-Pfad). Gibt
    (ticker, [event,...]) zurück; jedes Event trägt sein Datum mit, damit der Elternprozess
    daraus `by_date` zusammenführen kann."""
    strategy_key, ticker, df, start_after, allow_short, clock = args
    strategy = strat_mod.get(strategy_key)
    events = []
    try:
        # Die letzte Tages-Bar kann noch laufen. Sie ist nie eine Entscheidungs-Bar.
        for i in range(WARMUP_BARS, len(df) - 1):
            if df.index[i] < start_after:
                continue
            clock.advance_to(df.index[i])
            sig = _generate(strategy, ticker, {"1d": df.iloc[:i + 1]}, allow_short)
            if sig and sig.get("direction") in ("long", "short") and sig.get("stop_loss"):
                events.append({
                    "ticker": ticker, "idx": i, "strength": sig.get("strength", 0.0),
                    "direction": sig["direction"], "entry": float(df["Close"].iloc[i]),
                    "sl": float(sig["stop_loss"]), "tp": float(sig["take_profit"]),
                    "date": str(df.index[i].date()),
                })
    except Exception as e:
        log.warning(f"gather_fires {ticker} fehlgeschlagen: {e}")
    return ticker, events


def _download_daily(tickers: list[str], years: int,
                    data_provider: MarketDataProvider | None = None) -> dict:
    """Lädt Tageskurse und gibt {ticker: bereinigtes OHLCV-DataFrame} zurück."""
    provider = data_provider or YFinanceResearchProvider()
    period = f"{years + 1}y"   # +1 Jahr Warmup-Puffer vor dem Testfenster
    out = {}
    for t in tickers:
        try:
            df = provider.get_bars(t, period=period, interval="1d")
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
                    allow_short: bool = False,
                    trail_mode: str | None = None, trail_mult: float | None = None,
                    cost_pct: float | None = None, cost_model: CostModel | None = None,
                    clock: Clock | None = None) -> list[dict]:
    """Simuliert eine Strategie für einen Ticker; gibt die Liste geschlossener Trades zurück.
    `sl_tp_mode` (passiv/normal/aggressiv) überschreibt – falls gesetzt – die SL/TP der Strategie
    einheitlich per ATR (für die Modus-Vergleichs-Reports).
    `allow_short` (nur Backtest) erlaubt zusätzlich Short-Trades (gespiegeltes SL/TP).
    `cost_pct` (% je Seite, None → Config BACKTEST_COST_PCT) zieht Spread/Slippage vom P&L ab."""
    cost_pct = _resolve_cost(cost_pct)
    cost_model = _resolve_cost_model(cost_pct, cost_model)
    clock = clock or BarClock()
    trades = []
    n = len(df)
    # Die letzte vom Provider gelieferte Tages-Bar gilt als potenziell unvollständig. Damit
    # liegen Signal und Entry stets nach Abschluss des Entscheidungs-Bars vor.
    last_decision_idx = n - 2
    i = warmup
    while i <= last_decision_idx:
        clock.advance_to(df.index[i])
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
        trailing = (trail_mode or "").lower() == "atr" and bool(trail_mult and trail_mult > 0)
        while j < n and (j - i) <= max_hold:
            lo, hi = float(df["Low"].iloc[j]), float(df["High"].iloc[j])
            sl = _update_trailing_stop(df, j, direction, sl, trail_mode, trail_mult)
            stop_reason = "Trailing-Stop" if trailing else "Stop-Loss"
            if direction == "long":
                if lo <= sl:                     # konservativ: SL vor TP, falls beide am selben Tag
                    exit_price, reason = sl, stop_reason; break
                if not trailing and hi >= tp:
                    exit_price, reason = tp, "Take-Profit"; break
            else:                                # short: SL liegt ÜBER, TP UNTER dem Einstieg
                if hi >= sl:
                    exit_price, reason = sl, stop_reason; break
                if not trailing and lo <= tp:
                    exit_price, reason = tp, "Take-Profit"; break
            j += 1
        if exit_price is None:
            j = min(j, n - 1)
            exit_price, reason = float(df["Close"].iloc[j]), "Zeitlimit"

        pnl_pct, pnl_eur, costs = _net_pnl_with_costs(
            entry, exit_price, direction, trade_size, 1.0, cost_model,
            entry_volume=float(df["Volume"].iloc[i]), exit_volume=float(df["Volume"].iloc[j]))
        trades.append({
            "ticker": ticker, "direction": direction, "entry": entry, "exit": exit_price,
            "pnl_pct": round(pnl_pct, 2), "pnl_eur": round(pnl_eur, 2),
            "reason": reason, "entry_date": str(df.index[i].date()),
            "exit_date": str(df.index[j].date()),
            "cost_breakdown": costs.as_dict(),
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
                 trade_size: float = TRADE_SIZE_EUR, allow_short: bool = False,
                 data: dict | None = None, jobs: int | None = None,
                 trail_mode: str | None = None, trail_mult: float | None = None,
                 cost_pct: float | None = None,
                 cost_model: CostModel | None = None,
                 data_provider: MarketDataProvider | None = None,
                 clock: Clock | None = None,
                 seed: int = DEFAULT_SEED, capture_metadata: bool = False) -> dict:
    """Führt einen Backtest aus und gibt {strategy, metrics, trades, n_tickers, years} zurück.
    Trades chronologisch (nach Ausstiegsdatum) für eine saubere Equity-/Drawdown-Kurve.
    `allow_short` (nur Standard-Strategie) testet zusätzlich Short-Setups.
    `data` (vorab geladen) überspringt den Download — so teilen sich mehrere Strategien einen
    Download. `jobs` parallelisiert über Ticker (None → Config/alle Kerne)."""
    strategy = strat_mod.get(strategy_key)
    if strategy.key == "ai_adaptive" and trail_mode is None and trail_mult is None:
        p = strat_mod.strategy_runtime_params("ai_adaptive")
        if p.get("trail_mult"):
            trail_mode, trail_mult = "atr", float(p["trail_mult"])
    if tickers is None:
        tickers = universes.get_tickers(DEFAULT_REGION, auto=False)   # kuratierter Korb
    if data is None:
        data = (_download_daily(tickers, years, data_provider) if data_provider is not None
                else _download_daily(tickers, years))

    cost_pct = _resolve_cost(cost_pct)
    cost_model = _resolve_cost_model(cost_pct, cost_model)
    if capture_metadata:
        set_seed(seed)
    clock = clock or BarClock()
    args = [(strategy.key, t, df, trade_size, MAX_HOLD_DAYS, WARMUP_BARS, None, allow_short,
             trail_mode, trail_mult, cost_pct, cost_model, clock)
            for t, df in data.items()]
    all_trades = []
    for trades in _pmap(_bt_one, args, jobs):
        all_trades.extend(trades)
    result = _assemble_result(strategy, len(data), years, allow_short, trade_size, all_trades,
                              cost_pct=cost_pct)
    if capture_metadata:
        result["run_metadata"] = capture_run_metadata(
            strategy_key=strategy.key, tickers=list(data.keys()), years=years,
            trade_size=trade_size, allow_short=allow_short, seed=seed,
            cost_model=_dc_asdict(cost_model), universe_region=DEFAULT_REGION,
            trail_mode=trail_mode, trail_mult=trail_mult,
        ).as_dict()
    return result


def _assemble_result(strategy, n_tickers: int, years: int, allow_short: bool,
                     trade_size: float, trades: list[dict],
                     cost_pct: float | None = None) -> dict:
    """Baut das Backtest-Ergebnis-Dict (Trades chronologisch nach Ausstiegsdatum)."""
    trades = sorted(trades, key=lambda x: x["exit_date"])
    return {
        "strategy":  strategy.key,
        "label":     strategy.label,
        "n_tickers": n_tickers,
        "years":     years,
        "allow_short": allow_short,
        "cost_pct":  _resolve_cost(cost_pct),
        "direction_split": _direction_split(trades),
        "metrics":   metrics_mod.compute_metrics(trades, initial_capital=trade_size * 10),
        "cost_breakdown": _total_cost_breakdown(trades),
        "trades":    trades,
    }


def _total_cost_breakdown(trades: list[dict]) -> dict:
    """Summiert ausweisbare Kostenfelder ueber alle Trades eines Reports."""
    fields = CostBreakdown.__dataclass_fields__
    totals = {name: sum(float(t.get("cost_breakdown", {}).get(name, 0.0)) for t in trades)
              for name in fields}
    totals["total"] = sum(float(t.get("cost_breakdown", {}).get("total", 0.0)) for t in trades)
    return totals


def compare_strategies(keys: list[str], tickers: list[str] | None = None, years: int = 2,
                       trade_size: float = TRADE_SIZE_EUR, allow_short: bool = False,
                       data: dict | None = None, jobs: int | None = None,
                       cost_pct: float | None = None,
                       cost_model: CostModel | None = None,
                       data_provider: MarketDataProvider | None = None,
                       clock: Clock | None = None) -> list[dict]:
    """Backtestet mehrere Strategien über denselben Korb/Zeitraum (sortiert nach Profitfaktor).
    Lädt die Kurse **einmal** (statt K× neu) und verteilt **alle (Strategie × Ticker)-Aufgaben
    über EINEN gemeinsamen Prozess-Pool** — so wird der Worker-Start-/Import-Overhead nur einmal
    bezahlt und alle Kerne bleiben über alle Strategien hinweg ausgelastet."""
    if tickers is None:
        tickers = universes.get_tickers(DEFAULT_REGION, auto=False)
    if data is None:
        data = (_download_daily(tickers, years, data_provider) if data_provider is not None
                else _download_daily(tickers, years))

    cost_pct = _resolve_cost(cost_pct)
    cost_model = _resolve_cost_model(cost_pct, cost_model)
    clock = clock or BarClock()
    items = list(data.items())
    T = len(items)
    args = [(k, t, df, trade_size, MAX_HOLD_DAYS, WARMUP_BARS, None, allow_short, None, None,
             cost_pct, cost_model, clock)
            for k in keys for t, df in items]
    flat = _pmap(_bt_one, args, jobs)            # ein Pool für ALLE Strategien×Ticker

    results = []
    for ki, k in enumerate(keys):                # Ergebnisse je Strategie zurückgruppieren
        trades = []
        for r in flat[ki * T:(ki + 1) * T]:
            trades.extend(r)
        results.append(_assemble_result(strat_mod.get(k), T, years, allow_short, trade_size, trades,
                                        cost_pct=cost_pct))

    def pf_key(r):
        pf = r["metrics"]["profit_factor"]
        return pf if pf is not None else float("inf")   # „kein Verlust" ganz nach oben

    results.sort(key=pf_key, reverse=True)
    return results


def _trailing_atr(df: pd.DataFrame, end_idx: int, period: int = 14) -> float | None:
    """ATR bis einschließlich end_idx; nur historische/geschlossene Bars, kein Future-Lookahead."""
    if end_idx <= 0:
        return None
    start = max(1, end_idx - period + 1)
    trs = []
    for k in range(start, end_idx + 1):
        hi = float(df["High"].iloc[k])
        lo = float(df["Low"].iloc[k])
        prev_close = float(df["Close"].iloc[k - 1])
        trs.append(max(hi - lo, abs(hi - prev_close), abs(lo - prev_close)))
    return float(sum(trs) / len(trs)) if trs else None


def _update_trailing_stop(df: pd.DataFrame, j: int, direction: str, current_stop: float,
                          trail_mode: str | None, trail_mult: float | None) -> float:
    if (trail_mode or "").lower() != "atr" or not trail_mult or trail_mult <= 0:
        return current_stop
    # Vor der Intraday-Prüfung von Bar j nur Daten bis Bar j-1 nutzen.
    atr = _trailing_atr(df, j - 1)
    if atr is None or atr <= 0:
        return current_stop
    prev_close = float(df["Close"].iloc[j - 1])
    if direction == "long":
        return max(current_stop, prev_close - float(trail_mult) * atr)
    return min(current_stop, prev_close + float(trail_mult) * atr)


# ── Portfolio-Backtest: top_n Signale/Tag + Hebel ────────────────────────────

def _walk_exit(df: pd.DataFrame, i: int, sl: float, tp: float, leverage: float,
               max_hold: int = MAX_HOLD_DAYS, direction: str = "long",
               trail_mode: str | None = None, trail_mult: float | None = None):
    """Ausstieg ab Bar i+1: Liquidation (Hebel) → SL → TP (intrabar via High/Low), sonst Zeitlimit.
    Bei `direction="short"` ist alles gespiegelt: SL liegt ÜBER, TP UNTER dem Einstieg, und
    liquidiert wird bei steigendem Kurs (Hoch ≥ Liquidationskurs)."""
    n = len(df)
    liq = evaluator.liquidation_price(float(df["Close"].iloc[i]), leverage, direction)
    trailing = (trail_mode or "").lower() == "atr" and bool(trail_mult and trail_mult > 0)
    j = i + 1
    while j < n and (j - i) <= max_hold:
        lo, hi = float(df["Low"].iloc[j]), float(df["High"].iloc[j])
        sl = _update_trailing_stop(df, j, direction, sl, trail_mode, trail_mult)
        stop_reason = "Trailing-Stop" if trailing else "Stop-Loss"
        if direction == "long":
            if liq is not None and lo <= liq:
                return j, liq, "Liquidation"
            if lo <= sl:
                return j, sl, stop_reason
            if not trailing and hi >= tp:
                return j, tp, "Take-Profit"
        else:                                # short: Liquidation/SL oben, TP unten
            if liq is not None and hi >= liq:
                return j, liq, "Liquidation"
            if hi >= sl:
                return j, sl, stop_reason
            if not trailing and lo <= tp:
                return j, tp, "Take-Profit"
        j += 1
    j = min(j, n - 1)
    return j, float(df["Close"].iloc[j]), "Zeitlimit"


def gather_fires(strategy, data: dict, start_after, allow_short: bool = False,
                 jobs: int | None = None, clock: Clock | None = None) -> dict:
    """Teurer Teil (einmal pro Strategie): je Handelstag die feuernden Signale sammeln.
    Gibt {date_str: [ {ticker, idx, strength, direction, entry, sl, tp, date}, ... ]} zurück.
    Parallelisiert über Ticker (`jobs`); die Reihenfolge je Datum bleibt wie seriell
    (Ticker-Reihenfolge erhalten → gleiche Top-N-Auswahl).
    `allow_short` (nur Standard-Strategie) sammelt zusätzlich Short-Setups."""
    clock = clock or BarClock()
    args = [(strategy.key, tkr, df, start_after, allow_short, clock) for tkr, df in data.items()]
    by_date: dict[str, list] = {}
    for _tkr, events in _pmap(_fires_one, args, jobs):
        for ev in events:
            by_date.setdefault(ev["date"], []).append(ev)
    return by_date


def simulate_portfolio(data: dict, by_date: dict, top_n: int, leverage: float,
                       trade_size: float, max_concurrent: int,
                       max_hold: int = MAX_HOLD_DAYS,
                       trail_mode: str | None = None, trail_mult: float | None = None,
                       cost_pct: float | None = None,
                       cost_model: CostModel | None = None) -> list[dict]:
    """Billiger Teil: aus den Feuer-Events die Trades simulieren (Hold = max_hold Tage).
    `max_hold=1` ≈ „am Tagesende schließen", großes max_hold = „halten bis SL/TP".
    Die Richtung (long/short) wird je Feuer-Event übernommen (Default long).
    `cost_pct` (% je Seite, None → Config) zieht Spread/Slippage vom P&L ab."""
    cost_pct = _resolve_cost(cost_pct)
    cost_model = _resolve_cost_model(cost_pct, cost_model)
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
                                              max_hold, direction, trail_mode=trail_mode,
                                              trail_mult=trail_mult)
            pnl_pct, pnl_eur, costs = _net_pnl_with_costs(
                s["entry"], ex_price, direction, trade_size, leverage, cost_model,
                entry_volume=float(df["Volume"].iloc[s["idx"]]),
                exit_volume=float(df["Volume"].iloc[ej]))
            open_pos[s["ticker"]] = df.index[ej]
            trades.append({
                "ticker": s["ticker"], "entry_date": d, "exit_date": str(df.index[ej].date()),
                "entry": s["entry"], "exit": ex_price, "pnl_pct": round(pnl_pct, 2),
                "pnl_eur": round(pnl_eur, 2), "reason": reason, "hold_days": (ej - s["idx"]),
                "direction": direction,
                "cost_breakdown": costs.as_dict(),
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
                       allow_short: bool = False, data: dict | None = None,
                       jobs: int | None = None,
                       trail_mode: str | None = None, trail_mult: float | None = None,
                       cost_pct: float | None = None,
                       cost_model: CostModel | None = None,
                       data_provider: MarketDataProvider | None = None,
                       clock: Clock | None = None) -> dict:
    """
    Portfolio-Simulation: pro Handelstag die besten `top_n` Signale (für noch nicht offene Ticker)
    eröffnen — mit `leverage`. Ausstieg via Liquidation/SL/TP/Zeitlimit (`max_hold`). Kein Look-ahead.

    Voll investiert: `max_concurrent` (Default top_n) begrenzt gleichzeitige Positionen,
    `trade_size` (Default initial_capital/top_n) ist die Margin je Position.
    `max_hold=1` ≈ „am Tagesende schließen".
    `allow_short` (nur Standard-Strategie, Backtest) nimmt zusätzlich Short-Setups auf.
    """
    strategy = strat_mod.get(strategy_key)
    if strategy.key == "ai_adaptive" and trail_mode is None and trail_mult is None:
        p = strat_mod.strategy_runtime_params("ai_adaptive")
        if p.get("trail_mult"):
            trail_mode, trail_mult = "atr", float(p["trail_mult"])
    if tickers is None:
        tickers = universes.get_tickers(DEFAULT_REGION, auto=False)
    if max_concurrent is None:
        max_concurrent = top_n
    if trade_size is None:
        trade_size = initial_capital / max(top_n, 1)

    if data is None:
        data = (_download_daily(tickers, years, data_provider) if data_provider is not None
                else _download_daily(tickers, years))
    if not data:
        return {"trades": [], "metrics": metrics_mod.compute_metrics([], initial_capital),
                "cost_breakdown": _total_cost_breakdown([])}

    last = max(df.index[-1] for df in data.values())
    start_after = last - pd.Timedelta(days=int(years * 365.25))
    cost_pct = _resolve_cost(cost_pct)
    cost_model = _resolve_cost_model(cost_pct, cost_model)
    by_date = gather_fires(strategy, data, start_after, allow_short=allow_short, jobs=jobs,
                           clock=clock)
    trades = simulate_portfolio(data, by_date, top_n, leverage, trade_size, max_concurrent, max_hold,
                                trail_mode=trail_mode, trail_mult=trail_mult, cost_pct=cost_pct,
                                cost_model=cost_model)

    return {
        "strategy": strategy.key, "label": strategy.label,
        "trades": trades, "metrics": metrics_mod.compute_metrics(trades, initial_capital),
        "cost_breakdown": _total_cost_breakdown(trades),
        "cost_pct": cost_pct,
        "leverage": leverage, "top_n": top_n, "years": years, "max_hold": max_hold,
        "allow_short": allow_short, "direction_split": _direction_split(trades),
        "n_tickers": len(data), "trade_size": trade_size, "initial_capital": initial_capital,
        "liquidations": sum(1 for t in trades if t["reason"] == "Liquidation"),
        "signals_per_day": signals_per_day(by_date),
        "start": str(start_after.date()), "end": str(last.date()),
    }
