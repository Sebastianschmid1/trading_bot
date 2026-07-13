"""Pure partial-fill decisions (Phase 4 / OMS-006, Plan.md §12.6).

This module defines policy only and performs no broker, database, or notification IO.  OMS-005
already projects cumulative fills into a partial ``Position`` and its existing notifier hook
updates consumers after each broker event.  A later live orchestration step may act on the
recommendations returned here.

In particular, ``resize_needed`` is deliberately advisory.  Automatically cancelling and
resubmitting a protective order could race with another fill; that unsafe operation is excluded
for the same reason that ``AlpacaBrokerAdapter.replace_order`` rejects synthetic cancel+submit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isclose
from typing import Literal

from stockbot.config import PARTIAL_FILL_TIMEOUT_MIN
from stockbot.core.domain import Order, Position, PositionStatus
from stockbot.core.post_trade_risk import check_open_position_has_protective_order


PartialFillAction = Literal[
    "submit_protective",
    "resize_needed",
    "no_action_needed",
    "cancel_restorder",
]


@dataclass(frozen=True)
class PartialFillDecision:
    """Recommended next action; ``target_qty`` is always the current filled exposure."""

    action: PartialFillAction
    target_qty: float
    remaining_qty: float | None = None
    reason: str = ""


def _as_open_position(position: Position) -> Position:
    """Make the RISK-005 check applicable to OMS-005's pending partial position."""
    return replace(position, status=PositionStatus.OPEN)


def decide_partial_fill_action(
    *,
    order: Order,
    position: Position,
    active_orders: tuple[Order, ...],
    remaining_qty: float | None,
    order_submitted_at: datetime,
    now: datetime,
    timeout_minutes: int = PARTIAL_FILL_TIMEOUT_MIN,
) -> PartialFillDecision:
    """Recommend protection or rest-order handling for the latest cumulative fill state.

    Rest-order cancellation takes priority once a positive remainder has been open for strictly
    longer than ``timeout_minutes``.  Otherwise RISK-005 is the authority for whether protection
    exists.  Because its check intentionally applies only to open positions, this function passes
    it an ``OPEN`` projection of the partial ``PENDING_OPEN`` position produced by OMS-005.

    If protection exists, its quantity is compared with the absolute current position quantity.
    A mismatch returns ``resize_needed`` but performs no replacement and no cancel+submit.  The
    existing broker-event notifier, not this policy, is responsible for user-status updates.
    """
    if order.user_id != position.user_id or order.ticker != position.ticker:
        raise ValueError("Position does not belong to the partially filled order")
    if position.qty == 0:
        raise ValueError("Partial-fill position quantity must be non-zero")
    if remaining_qty is not None and remaining_qty < 0:
        raise ValueError("Remaining quantity cannot be negative")
    if timeout_minutes < 0:
        raise ValueError("Partial-fill timeout cannot be negative")

    target_qty = abs(float(position.qty))
    if (remaining_qty is not None and remaining_qty > 0
            and now - order_submitted_at > timedelta(minutes=timeout_minutes)):
        return PartialFillDecision(
            action="cancel_restorder",
            target_qty=target_qty,
            remaining_qty=remaining_qty,
            reason="Unausgefüllte Restmenge hat den Partial-Fill-Timeout überschritten.",
        )

    risk_position = _as_open_position(position)
    protection = check_open_position_has_protective_order(
        position=risk_position, active_orders=active_orders,
    )
    if not protection.ok:
        return PartialFillDecision(
            action="submit_protective",
            target_qty=target_qty,
            remaining_qty=remaining_qty,
            reason=protection.reason,
        )

    # Ask the shared RISK-005 rule which individual orders count as protection.  This avoids a
    # second implementation of its side, ownership, symbol, and active-status criteria.
    protective_orders = tuple(
        candidate for candidate in active_orders
        if check_open_position_has_protective_order(
            position=risk_position, active_orders=(candidate,),
        ).ok
    )
    quantities_match = any(
        candidate.qty is not None
        and isclose(float(candidate.qty), target_qty, rel_tol=1e-9, abs_tol=1e-12)
        for candidate in protective_orders
    )
    if quantities_match:
        return PartialFillDecision(
            action="no_action_needed",
            target_qty=target_qty,
            remaining_qty=remaining_qty,
            reason="Aktive Schutzorder deckt die aktuelle Fillmenge ab.",
        )
    return PartialFillDecision(
        action="resize_needed",
        target_qty=target_qty,
        remaining_qty=remaining_qty,
        reason="Aktive Schutzorder deckt die aktuelle Fillmenge nicht ab; sichere Anpassung nötig.",
    )
