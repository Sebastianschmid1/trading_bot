"""Pure strategy-exit policies (Phase 5 / STRAT-005, Plan.md section 13.6).

The functions in this module only recommend an exit.  They perform no database, broker,
calendar, market-data, or notification IO and are deliberately not wired into a live path yet.
A later, explicitly approved orchestration change can call :func:`evaluate_strategy_exit` from
``stockbot/tgbot/bot.py::evaluate_active_trade`` or its surrounding monitor and pass in market
data plus ``exchange_calendar.minutes_to_close``.

Liquidation, the fixed stop-loss, and take-profit remain authoritative in the existing
``evaluate_active_trade`` path; these policies complement rather than replace those protections.
Likewise, earnings/event-calendar lookup is outside this module: callers inject the boolean
``blocking_event`` hook.  Every individual rule is skipped when its required inputs are absent.

This module lives below ``stockbot.market`` because it reuses the established MACD and
SuperTrend helpers.  Placing it in ``stockbot.core`` would invert the existing core -> market
dependency boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from stockbot.market import analyzer, strategies


@dataclass(frozen=True)
class ExitDecision:
    """Advisory result of a strategy-specific exit check."""

    close: bool
    reason: str = ""
    code: str = ""


_HOLD = ExitDecision(close=False)


def _close(reason: str, code: str) -> ExitDecision:
    return ExitDecision(close=True, reason=reason, code=code)


def _closes(bars: pd.DataFrame | None) -> np.ndarray | None:
    """Return finite closes without mutating caller-owned bars."""
    if bars is None or "Close" not in bars.columns:
        return None
    values = pd.to_numeric(bars["Close"], errors="coerce").dropna().to_numpy(dtype=float)
    return values if len(values) else None


def _trailing_stop(
    *, current_price: float | None, highest_price_since_entry: float | None,
    atr: float | None, trailing_atr_multiple: float,
) -> ExitDecision:
    if current_price is None or highest_price_since_entry is None or atr is None or atr <= 0:
        return _HOLD
    threshold = highest_price_since_entry - trailing_atr_multiple * atr
    if current_price <= threshold:
        return _close(
            f"Kurs {current_price:.2f} am/unter ATR-Trailing-Stop {threshold:.2f}.",
            "trailing_stop",
        )
    return _HOLD


def momentum_exit(
    *,
    entry_price: float | None = None,
    current_price: float | None = None,
    highest_price_since_entry: float | None = None,
    atr: float | None = None,
    bars: pd.DataFrame | None = None,
    opened_at: datetime | None = None,
    now: datetime | None = None,
    minutes_to_close: float | None = None,
    trailing_atr_multiple: float = 3.0,
    entry_timeout_minutes: int = 390,
    minimum_gain_pct: float = 0.0,
    market_close_minutes: int = 15,
) -> ExitDecision:
    """Evaluate the ``standard`` intraday-momentum exits in deterministic order.

    Momentum break is the intentionally simple proxy MACD histogram < 0 and price < MA20.
    Entry timeout applies after one regular US trading session by default, but only while the
    position has not reached ``minimum_gain_pct``.  ``minutes_to_close`` must be supplied by the
    caller's exchange-calendar integration.
    """
    decision = _trailing_stop(
        current_price=current_price,
        highest_price_since_entry=highest_price_since_entry,
        atr=atr,
        trailing_atr_multiple=trailing_atr_multiple,
    )
    if decision.close:
        return decision

    closes = _closes(bars)
    if closes is not None and len(closes) >= 26:
        price = current_price if current_price is not None else float(closes[-1])
        ma20 = float(np.mean(closes[-20:]))
        _, _, histogram = analyzer.calc_macd(closes)
        if histogram < 0 and price < ma20:
            return _close("MACD-Histogramm negativ und Kurs unter MA20.", "momentum_break")

    if (
        opened_at is not None and now is not None and entry_price is not None
        and current_price is not None and entry_price > 0
        and now - opened_at > timedelta(minutes=entry_timeout_minutes)
    ):
        gain_pct = (current_price / entry_price - 1.0) * 100.0
        if gain_pct < minimum_gain_pct:
            return _close(
                f"Entry-Timeout überschritten; Gewinn {gain_pct:.2f}% unter Mindestwert "
                f"{minimum_gain_pct:.2f}%.",
                "entry_timeout",
            )

    if minutes_to_close is not None and minutes_to_close <= market_close_minutes:
        return _close(
            f"Nur noch {minutes_to_close:g} Minuten bis zum Marktende.", "market_close",
        )
    return _HOLD


def swing_trend_exit(
    *,
    current_price: float | None = None,
    highest_price_since_entry: float | None = None,
    atr: float | None = None,
    bars: pd.DataFrame | None = None,
    opened_at: datetime | None = None,
    now: datetime | None = None,
    blocking_event: bool | None = None,
    trailing_atr_multiple: float = 3.0,
    supertrend_atr_period: int = 10,
    supertrend_factor: float = 3.0,
    supertrend_lookback: int = 250,
    max_hold_days: int = 40,
) -> ExitDecision:
    """Evaluate ``ai_adaptive`` swing exits using SuperTrend/MA200 structure proxies."""
    if blocking_event is True:
        return _close("Blockierendes Ereignis für die Swing-Position gemeldet.", "event_filter")

    decision = _trailing_stop(
        current_price=current_price,
        highest_price_since_entry=highest_price_since_entry,
        atr=atr,
        trailing_atr_multiple=trailing_atr_multiple,
    )
    if decision.close:
        return decision

    closes = _closes(bars)
    if bars is not None:
        direction = strategies._supertrend_dir(
            bars,
            atr_period=supertrend_atr_period,
            factor=supertrend_factor,
            lookback=supertrend_lookback,
        ) if {"High", "Low", "Close"}.issubset(bars.columns) else None
        bearish_supertrend = direction is not None and direction[-1] < 0
        price = current_price if current_price is not None else (
            float(closes[-1]) if closes is not None else None
        )
        below_ma200 = (
            closes is not None and len(closes) >= 200 and price is not None
            and price < float(np.mean(closes[-200:]))
        )
        if bearish_supertrend or below_ma200:
            detail = "SuperTrend bearish" if bearish_supertrend else "Kurs unter MA200"
            return _close(f"Swing-Strukturbruch: {detail}.", "structure_break")

    if opened_at is not None and now is not None and now - opened_at > timedelta(days=max_hold_days):
        return _close(f"Maximale Haltedauer von {max_hold_days} Tagen überschritten.", "max_hold")
    return _HOLD


def mean_reversion_exit(
    *,
    current_price: float | None = None,
    bars: pd.DataFrame | None = None,
    opened_at: datetime | None = None,
    now: datetime | None = None,
    bollinger_period: int = 20,
    bollinger_std_multiple: float = 2.0,
    time_exit_days: int = 10,
) -> ExitDecision:
    """Evaluate ``bb_revert`` exits: mean return, MA200 regime break, then time exit."""
    closes = _closes(bars)
    price = current_price if current_price is not None else (
        float(closes[-1]) if closes is not None else None
    )
    if closes is not None and price is not None and len(closes) >= bollinger_period:
        window = closes[-bollinger_period:]
        mid = float(np.mean(window))
        std = float(np.std(window))
        upper = mid + bollinger_std_multiple * std
        lower = mid - bollinger_std_multiple * std
        pct_b = (price - lower) / (upper - lower) if upper > lower else None
        if (pct_b is not None and pct_b >= 0.5) or price >= mid:
            return _close("Kurs ist zur Bollinger-Bandmitte/MA20 zurückgekehrt.", "mean_reverted")

    if closes is not None and price is not None and len(closes) >= 200:
        ma200 = float(np.mean(closes[-200:]))
        if price < ma200:
            return _close("Mean-Reversion-Schutzregime gebrochen: Kurs unter MA200.", "regime_break")

    if opened_at is not None and now is not None and now - opened_at > timedelta(days=time_exit_days):
        return _close(f"Zeit-Exit nach mehr als {time_exit_days} Tagen.", "time_exit")
    return _HOLD


def evaluate_strategy_exit(
    strategy_key: str,
    *,
    family: str | None = None,
    **inputs,
) -> ExitDecision:
    """Dispatch by an injected family or ``strategies.REGISTRY[strategy_key].family``.

    Unknown, research-only, and deprecated strategies intentionally have no production policy.
    """
    strategy = strategies.REGISTRY.get(strategy_key)
    resolved_family = family if family is not None else (
        strategy.family if strategy is not None else "research_only"
    )
    policies = {
        "intraday_momentum": momentum_exit,
        "swing_trend": swing_trend_exit,
        "mean_reversion": mean_reversion_exit,
    }
    policy = policies.get(resolved_family)
    if policy is None:
        return ExitDecision(
            close=False,
            reason="Für diese Strategie-Familie ist keine Produktions-Exit-Policy definiert.",
            code="no_policy",
        )
    return policy(**inputs)
