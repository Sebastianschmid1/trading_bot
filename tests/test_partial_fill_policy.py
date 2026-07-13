"""Tests for the pure OMS-006 partial-fill policy."""

from datetime import datetime, timedelta, timezone

from stockbot.core.domain import Mode, Order, OrderStatus, Position, PositionStatus
from stockbot.execution.partial_fill_policy import decide_partial_fill_action


SUBMITTED_AT = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)


def _entry_order() -> Order:
    return Order(
        id=1, trade_intent_id=2, user_id=7, ticker="AAPL", side="buy", qty=10.0,
        status=OrderStatus.PARTIALLY_FILLED,
    )


def _position(*, qty: float = 4.0) -> Position:
    return Position(
        id=3, user_id=7, ticker="AAPL", strategy_version_id=4, mode=Mode.PAPER,
        status=PositionStatus.PENDING_OPEN, qty=qty,
    )


def _protective_order(*, qty: float) -> Order:
    return Order(
        id=5, trade_intent_id=2, user_id=7, ticker="AAPL", side="sell", qty=qty,
        status=OrderStatus.ACCEPTED_BY_BROKER,
    )


def _decide(*, position=None, active_orders=(), remaining_qty=6.0, elapsed_minutes=5):
    return decide_partial_fill_action(
        order=_entry_order(),
        position=position or _position(),
        active_orders=active_orders,
        remaining_qty=remaining_qty,
        order_submitted_at=SUBMITTED_AT,
        now=SUBMITTED_AT + timedelta(minutes=elapsed_minutes),
        timeout_minutes=15,
    )


def test_missing_protective_order_recommends_current_fill_quantity():
    decision = _decide(active_orders=(_entry_order(),))

    assert decision.action == "submit_protective"
    assert decision.target_qty == 4.0


def test_matching_protective_order_needs_no_action():
    decision = _decide(active_orders=(_protective_order(qty=4.0),))

    assert decision.action == "no_action_needed"
    assert decision.target_qty == 4.0


def test_stale_protective_quantity_after_later_fill_requires_safe_resize():
    decision = _decide(
        position=_position(qty=7.0), active_orders=(_protective_order(qty=4.0),),
        remaining_qty=3.0,
    )

    assert decision.action == "resize_needed"
    assert decision.target_qty == 7.0


def test_timed_out_positive_remainder_recommends_cancellation():
    decision = _decide(active_orders=(), remaining_qty=6.0, elapsed_minutes=16)

    assert decision.action == "cancel_restorder"
    assert decision.remaining_qty == 6.0


def test_positive_remainder_before_timeout_does_not_recommend_cancellation():
    decision = _decide(active_orders=(), remaining_qty=6.0, elapsed_minutes=14)

    assert decision.action == "submit_protective"
    assert decision.action != "cancel_restorder"
