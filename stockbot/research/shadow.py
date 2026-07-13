"""IO-free shadow-signal simulation and mode-isolated evaluation (RES-001).

The future scheduler seam is intentionally narrow: a live-data scheduler obtains bars/quotes,
calls a strategy, passes its dict through :func:`to_shadow_signal`, persists that domain signal,
and supplies subsequent bars to the simulation functions here.  This module itself never loads
market data, writes storage, or talks to a broker.

Exit simulation mirrors the conservative daily backtest convention without importing the
IO-heavy backtest engine: supplied bars are *subsequent* bars (the signal/entry bar is not part
of them), and each is checked in Liquidation -> Stop-Loss -> Take-Profit order.  If both stop and
target occur in one bar, the stop wins.  An untouched position exits at the final available
close (or at ``max_hold_bars``) as a time-limit exit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from numbers import Real
from typing import Any, Iterable, Mapping

from stockbot.core.domain import Mode, PerformanceSnapshot, Position, Signal, SignalStatus


_SHADOW_SIGNAL_STATUSES = frozenset({SignalStatus.GENERATED, SignalStatus.PUBLISHED})


@dataclass(frozen=True)
class SimulatedFill:
    """Adverse simulated shadow entry; no broker fill or executable order exists."""

    signal_id: int | None
    strategy_version_id: int
    ticker: str
    direction: str
    reference_price: float
    price: float
    qty: float = 1.0
    filled_at: str | None = None
    slippage_bps: float = 0.0
    fee: float = 0.0
    mode: Mode = Mode.SHADOW


@dataclass(frozen=True)
class SimulatedExit:
    """Closed result of a simulated shadow position."""

    entry: SimulatedFill
    price: float
    reason: str
    pnl: float
    exited_at: str | None = None
    bar_index: int | None = None
    fee: float = 0.0
    mode: Mode = Mode.SHADOW


def _shadow_status(value: SignalStatus | str) -> SignalStatus:
    status = SignalStatus(value)
    if status not in _SHADOW_SIGNAL_STATUSES:
        allowed = ", ".join(sorted(item.value for item in _SHADOW_SIGNAL_STATUSES))
        raise ValueError(f"Shadow signals may only be created as {allowed}")
    return status


def to_shadow_signal(
    signal: Signal | Mapping[str, Any], *,
    strategy_version_id: int | None = None,
    status: SignalStatus | str = SignalStatus.GENERATED,
    signal_id: int | None = None,
    generated_at: str | None = None,
    published_at: str | None = None,
) -> Signal:
    """Convert one strategy result or domain signal into a full SHADOW signal.

    Strategy dictionaries do not currently carry a version id, so their caller must supply
    ``strategy_version_id`` (unless the mapping already contains it).  Only the two lifecycle
    states relevant at this seam are accepted: ``generated`` and ``published``.  Publication
    still happens in the scheduler/persistence layer; this pure converter records its result.
    """
    target_status = _shadow_status(status)
    if isinstance(signal, Signal):
        return replace(
            signal,
            mode=Mode.SHADOW,
            status=target_status,
            strategy_version_id=(signal.strategy_version_id
                                 if strategy_version_id is None else int(strategy_version_id)),
            id=signal.id if signal_id is None else signal_id,
            generated_at=signal.generated_at if generated_at is None else generated_at,
            published_at=signal.published_at if published_at is None else published_at,
        )
    if not isinstance(signal, Mapping):
        raise TypeError("signal must be a domain Signal or strategy-result mapping")

    version = (strategy_version_id if strategy_version_id is not None
               else signal.get("strategy_version_id"))
    if version is None:
        raise ValueError("strategy_version_id is required for a strategy-result mapping")
    ticker = str(signal.get("ticker") or "").strip()
    direction = str(signal.get("direction") or "").strip().lower()
    if not ticker or direction not in {"long", "short"}:
        raise ValueError("strategy result requires ticker and direction long/short")

    return Signal(
        id=signal_id if signal_id is not None else signal.get("id"),
        strategy_version_id=int(version),
        ticker=ticker,
        direction=direction,
        mode=Mode.SHADOW,
        status=target_status,
        candidate_id=signal.get("candidate_id"),
        data_provider=signal.get("data_provider"),
        data_feed=signal.get("data_feed"),
        data_fetched_at=signal.get("data_fetched_at"),
        data_exchange_time=signal.get("data_exchange_time", signal.get("as_of")),
        data_version=signal.get("data_version"),
        data_quality_status=signal.get("data_quality_status"),
        generated_at=generated_at if generated_at is not None else signal.get("generated_at"),
        published_at=published_at if published_at is not None else signal.get("published_at"),
        expires_at=signal.get("expires_at"),
    )


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _price(value: Any) -> float:
    if isinstance(value, Real) and not isinstance(value, bool):
        return _positive_number(value, "price")
    if isinstance(value, Mapping):
        for key in ("price", "Price", "close", "Close"):
            if value.get(key) is not None:
                return _positive_number(value[key], "price")
    if getattr(value, "price", None) is not None:
        return _positive_number(value.price, "price")
    raise TypeError("quote_or_price must be a positive number, Quote, or price mapping")


def simulate_entry(
    signal: Signal, *, quote_or_price: Any, slippage_bps: float = 0.0,
    qty: float = 1.0, filled_at: str | None = None, fee: float = 0.0,
) -> SimulatedFill:
    """Create an adverse fill at signal price plus side-aware slippage.

    Long entries pay above the reference price; short entries sell below it.  Slippage is
    expressed in basis points and can never improve a simulated fill.
    """
    if not isinstance(signal, Signal) or signal.mode != Mode.SHADOW:
        raise ValueError("simulate_entry requires a SHADOW domain Signal")
    direction = signal.direction.lower()
    if direction not in {"long", "short"}:
        raise ValueError("signal direction must be long or short")
    bps = float(slippage_bps)
    if not math.isfinite(bps) or bps < 0:
        raise ValueError("slippage_bps must not be negative")
    quantity = _positive_number(qty, "qty")
    reference = _price(quote_or_price)
    if filled_at is None:
        source_time = (quote_or_price.get("as_of") if isinstance(quote_or_price, Mapping)
                       else getattr(quote_or_price, "as_of", None))
        if source_time is not None:
            filled_at = (source_time.isoformat() if hasattr(source_time, "isoformat")
                         else str(source_time))
    adverse_sign = 1.0 if direction == "long" else -1.0
    executed = reference * (1.0 + adverse_sign * bps / 10_000.0)
    return SimulatedFill(
        signal_id=signal.id,
        strategy_version_id=signal.strategy_version_id,
        ticker=signal.ticker,
        direction=direction,
        reference_price=reference,
        price=executed,
        qty=quantity,
        filled_at=filled_at,
        slippage_bps=bps,
        fee=float(fee),
    )


def _mapping_value(row: Mapping[str, Any], name: str) -> Any:
    return row.get(name, row.get(name.capitalize()))


def _bar_rows(bars: Any) -> list[tuple[str | None, Mapping[str, Any]]]:
    if isinstance(bars, Mapping):
        return [(None, bars)]
    if hasattr(bars, "iterrows"):
        return [(str(index), row) for index, row in bars.iterrows()]
    try:
        rows = list(bars)
    except TypeError as exc:
        raise TypeError("bars_or_price must be a price, bar mapping, iterable, or DataFrame") from exc
    output = []
    for row in rows:
        if not isinstance(row, Mapping) and not hasattr(row, "get"):
            raise TypeError("each bar must provide high, low, and close")
        timestamp = _mapping_value(row, "timestamp") or _mapping_value(row, "time")
        output.append((str(timestamp) if timestamp is not None else None, row))
    return output


def _exit_result(
    entry: SimulatedFill, price: float, reason: str, *, exited_at: str | None,
    bar_index: int | None, fee: float,
) -> SimulatedExit:
    direction_sign = 1.0 if entry.direction == "long" else -1.0
    gross = (price - entry.price) * entry.qty * direction_sign
    pnl = gross - entry.fee - fee
    return SimulatedExit(
        entry=entry, price=price, reason=reason, pnl=pnl,
        exited_at=exited_at, bar_index=bar_index, fee=fee,
    )


def simulate_exit(
    position_or_entry: SimulatedFill | Position, *, bars_or_price: Any,
    stop_loss: float, take_profit: float,
    liquidation_price: float | None = None, max_hold_bars: int | None = None,
    exited_at: str | None = None, fee: float = 0.0,
) -> SimulatedExit:
    """Simulate a conservative shadow exit over bars following the entry bar.

    ``Position`` is accepted only when it is a SHADOW position with an entry price; because it
    lacks direction in today's domain model, it is treated as long.  Prefer ``SimulatedFill``
    when evaluating strategy output so short direction and entry costs remain explicit.
    """
    if isinstance(position_or_entry, Position):
        if position_or_entry.mode != Mode.SHADOW or position_or_entry.avg_entry_price is None:
            raise ValueError("Position must be SHADOW and have avg_entry_price")
        entry = SimulatedFill(
            signal_id=None,
            strategy_version_id=position_or_entry.strategy_version_id,
            ticker=position_or_entry.ticker,
            direction="long",
            reference_price=float(position_or_entry.avg_entry_price),
            price=float(position_or_entry.avg_entry_price),
            qty=_positive_number(position_or_entry.qty, "position qty"),
            filled_at=position_or_entry.opened_at,
        )
    elif isinstance(position_or_entry, SimulatedFill):
        entry = position_or_entry
        if entry.mode != Mode.SHADOW:
            raise ValueError("entry must be SHADOW")
    else:
        raise TypeError("position_or_entry must be SimulatedFill or Position")

    stop = _positive_number(stop_loss, "stop_loss")
    target = _positive_number(take_profit, "take_profit")
    liquidation = (None if liquidation_price is None
                   else _positive_number(liquidation_price, "liquidation_price"))
    if max_hold_bars is not None and max_hold_bars <= 0:
        raise ValueError("max_hold_bars must be greater than zero")

    if isinstance(bars_or_price, Real) and not isinstance(bars_or_price, bool):
        price = _positive_number(bars_or_price, "exit price")
        return _exit_result(
            entry, price, "market", exited_at=exited_at, bar_index=None, fee=float(fee),
        )

    rows = _bar_rows(bars_or_price)
    if not rows:
        raise ValueError("at least one subsequent bar is required")
    limit = len(rows) if max_hold_bars is None else min(len(rows), max_hold_bars)
    for index, (timestamp, row) in enumerate(rows[:limit]):
        low = _positive_number(_mapping_value(row, "low"), "bar low")
        high = _positive_number(_mapping_value(row, "high"), "bar high")
        if low > high:
            raise ValueError("bar low must not exceed high")
        when = timestamp or exited_at
        if entry.direction == "long":
            if liquidation is not None and low <= liquidation:
                return _exit_result(entry, liquidation, "liquidation", exited_at=when,
                                    bar_index=index, fee=float(fee))
            if low <= stop:
                return _exit_result(entry, stop, "stop_loss", exited_at=when,
                                    bar_index=index, fee=float(fee))
            if high >= target:
                return _exit_result(entry, target, "take_profit", exited_at=when,
                                    bar_index=index, fee=float(fee))
        else:
            if liquidation is not None and high >= liquidation:
                return _exit_result(entry, liquidation, "liquidation", exited_at=when,
                                    bar_index=index, fee=float(fee))
            if high >= stop:
                return _exit_result(entry, stop, "stop_loss", exited_at=when,
                                    bar_index=index, fee=float(fee))
            if low <= target:
                return _exit_result(entry, target, "take_profit", exited_at=when,
                                    bar_index=index, fee=float(fee))

    timestamp, final = rows[limit - 1]
    close = _positive_number(_mapping_value(final, "close"), "bar close")
    return _exit_result(entry, close, "time_limit", exited_at=timestamp or exited_at,
                        bar_index=limit - 1, fee=float(fee))


def shadow_performance_snapshot(
    fills_or_exits: Iterable[SimulatedFill | SimulatedExit] = (),
    exits: Iterable[SimulatedExit] | None = None, *, captured_at: str | None = None,
    strategy_version_id: int | None = None, open_risk: float | None = None,
) -> PerformanceSnapshot:
    """Aggregate explicit simulated fills/exits into one SHADOW performance observation.

    For the common closed-trade case, pass exits as the first argument.  The two-positional-
    argument form accepts ``fills, exits`` and is useful when a caller retains both streams.
    Fill-only input produces no invented P&L (``net_pnl=None``).
    """
    first = tuple(fills_or_exits)
    if exits is None:
        fills = tuple(item for item in first if isinstance(item, SimulatedFill))
        closed = tuple(item for item in first if isinstance(item, SimulatedExit))
        if len(fills) + len(closed) != len(first):
            raise ValueError("snapshot inputs must be SimulatedFill or SimulatedExit objects")
    else:
        fills = first
        closed = tuple(exits)
        if any(not isinstance(item, SimulatedFill) for item in fills):
            raise ValueError("first snapshot input must contain SimulatedFill objects")
    for fill in fills:
        if fill.mode != Mode.SHADOW:
            raise ValueError("all fills must be SHADOW SimulatedFill objects")
    for result in closed:
        if not isinstance(result, SimulatedExit) or result.mode != Mode.SHADOW:
            raise ValueError("all exits must be SHADOW SimulatedExit objects")
    versions = ({fill.strategy_version_id for fill in fills}
                | {result.entry.strategy_version_id for result in closed})
    if strategy_version_id is None:
        if len(versions) != 1:
            raise ValueError("strategy_version_id is required unless exits share one version")
        strategy_version_id = versions.pop()
    elif any(version != strategy_version_id for version in versions):
        raise ValueError("exit strategy version does not match snapshot strategy version")
    if captured_at is None:
        captured_at = next(
            (result.exited_at for result in reversed(closed) if result.exited_at), None,
        )
    if captured_at is None:
        captured_at = next((fill.filled_at for fill in reversed(fills) if fill.filled_at), None)
    if captured_at is None:
        raise ValueError("captured_at is required when exits have no exit timestamp")
    return PerformanceSnapshot(
        id=None,
        strategy_version_id=int(strategy_version_id),
        mode=Mode.SHADOW,
        captured_at=captured_at,
        net_pnl=sum(result.pnl for result in closed) if closed else None,
        open_risk=open_risk,
    )
