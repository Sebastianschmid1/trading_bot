"""Tests für den IO-freien Post-Trade-Risk-Check (RISK-005)."""

import pytest

from stockbot.core.domain import Mode, Order, OrderStatus, Position, PositionStatus
from stockbot.core.post_trade_risk import check_open_position_has_protective_order


def _position(*, qty=10.0, status=PositionStatus.OPEN):
    return Position(
        id=1, user_id=7, ticker="AAPL", strategy_version_id=3, mode=Mode.PAPER,
        status=status, qty=qty,
    )


def _order(*, side="sell", status=OrderStatus.ACCEPTED_BY_BROKER,
           user_id=7, ticker="AAPL"):
    return Order(
        id=2, trade_intent_id=4, user_id=user_id, ticker=ticker, side=side,
        qty=10.0, status=status,
    )


def test_open_long_position_with_active_sell_order_is_protected():
    assert check_open_position_has_protective_order(
        position=_position(), active_orders=(_order(),)).ok is True


def test_open_short_position_with_active_buy_order_is_protected():
    assert check_open_position_has_protective_order(
        position=_position(qty=-10.0), active_orders=(_order(side="buy"),)).ok is True


def test_open_position_without_orders_reports_missing_protection():
    decision = check_open_position_has_protective_order(
        position=_position(), active_orders=())
    assert decision.ok is False
    assert decision.code == "protective_order_missing"
    assert "AAPL" in decision.reason


@pytest.mark.parametrize("status", [
    OrderStatus.CREATED,
    OrderStatus.VALIDATED,
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
])
def test_inactive_or_terminal_order_does_not_protect(status):
    decision = check_open_position_has_protective_order(
        position=_position(), active_orders=(_order(status=status),))
    assert decision.ok is False
    assert decision.code == "protective_order_missing"


@pytest.mark.parametrize("status", [
    OrderStatus.SUBMITTED,
    OrderStatus.ACCEPTED_BY_BROKER,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.CANCEL_REQUESTED,
])
def test_broker_active_order_statuses_protect(status):
    assert check_open_position_has_protective_order(
        position=_position(), active_orders=(_order(status=status),)).ok is True


def test_entry_side_order_does_not_protect_position():
    decision = check_open_position_has_protective_order(
        position=_position(), active_orders=(_order(side="buy"),))
    assert decision.ok is False and decision.code == "protective_order_missing"


@pytest.mark.parametrize("order", [
    _order(ticker="MSFT"),
    _order(user_id=8),
])
def test_order_for_other_position_does_not_protect(order):
    decision = check_open_position_has_protective_order(
        position=_position(), active_orders=(order,))
    assert decision.ok is False and decision.code == "protective_order_missing"


@pytest.mark.parametrize("status", [
    PositionStatus.PENDING_OPEN,
    PositionStatus.PENDING_CLOSE,
    PositionStatus.CLOSED,
    PositionStatus.RECONCILIATION_REQUIRED,
])
def test_non_open_position_is_not_applicable(status):
    assert check_open_position_has_protective_order(
        position=_position(status=status), active_orders=()).ok is True


def test_open_position_with_zero_qty_is_invalid():
    decision = check_open_position_has_protective_order(
        position=_position(qty=0.0), active_orders=())
    assert decision.ok is False
    assert decision.code == "invalid_open_position_qty"
