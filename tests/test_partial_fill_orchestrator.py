from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stockbot.broker.client import submit_stop_sell
from stockbot.core.domain import Mode, Order, OrderStatus, Position, PositionStatus
from stockbot.execution.partial_fill_orchestrator import orchestrate_partial_fill


SUBMITTED = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)


def _order():
    return Order(
        id=11, trade_intent_id=12, user_id=7, ticker="AAPL", side="buy", qty=10,
        status=OrderStatus.PARTIALLY_FILLED, broker_order_id="entry-1",
    )


def _position(qty=4):
    return Position(
        id=None, user_id=7, ticker="AAPL", strategy_version_id=1, mode=Mode.PAPER,
        status=PositionStatus.PENDING_OPEN, qty=qty,
    )


def _run(*, active_orders=(), position=None, remaining_qty=6, minutes=5,
         stop_loss=95, submitted=None, canceled=None, persisted=None, notified=None):
    submitted = submitted if submitted is not None else []
    canceled = canceled if canceled is not None else []
    persisted = persisted if persisted is not None else []
    notified = notified if notified is not None else []

    def submit(symbol, **kwargs):
        submitted.append((symbol, kwargs))
        return {"ok": True, "id": "stop-1", "detail": "ok"}

    return orchestrate_partial_fill(
        order=_order(), position=position or _position(), remaining_qty=remaining_qty,
        order_submitted_at=SUBMITTED, now=SUBMITTED + timedelta(minutes=minutes),
        active_orders=active_orders, signal_stop_loss=stop_loss,
        submit_stop_sell=submit,
        cancel_order=lambda order_id: canceled.append(order_id) or {"ok": True},
        persist_protective=lambda **kwargs: persisted.append(kwargs),
        notifier=notified.append,
    )


def test_submit_protective_uses_fill_quantity_and_signal_stop_and_persists():
    submitted, persisted = [], []

    result = _run(submitted=submitted, persisted=persisted)

    assert result.decision.action == "submit_protective"
    assert submitted == [("AAPL", {"qty": 4.0, "stop_price": 95.0})]
    assert persisted[0]["qty"] == 4.0
    assert persisted[0]["broker_order_id"] == "stop-1"


def test_persisted_protection_prevents_second_stop_order():
    submitted, persisted = [], []
    _run(submitted=submitted, persisted=persisted)
    protective = Order(
        id=21, trade_intent_id=12, user_id=7, ticker="AAPL", side="sell", qty=4,
        status=OrderStatus.ACCEPTED_BY_BROKER, broker_order_id=persisted[0]["broker_order_id"],
    )

    second = _run(active_orders=(protective,), submitted=submitted, persisted=persisted)

    assert second.decision.action == "no_action_needed"
    assert len(submitted) == 1


def test_timeout_cancels_entry_restorder():
    canceled = []

    result = _run(minutes=16, canceled=canceled)

    assert result.decision.action == "cancel_restorder"
    assert canceled == ["entry-1"]


def test_resize_only_notifies_admin():
    submitted, canceled, notified = [], [], []
    protective = Order(
        id=21, trade_intent_id=12, user_id=7, ticker="AAPL", side="sell", qty=4,
        status=OrderStatus.ACCEPTED_BY_BROKER,
    )

    result = _run(
        position=_position(7), remaining_qty=3, active_orders=(protective,),
        submitted=submitted, canceled=canceled, notified=notified,
    )

    assert result.decision.action == "resize_needed"
    assert notified and submitted == [] and canceled == []


def test_missing_signal_stop_notifies_without_broker_order():
    submitted, notified = [], []

    result = _run(stop_loss=None, submitted=submitted, notified=notified)

    assert result.decision.action == "submit_protective"
    assert notified and submitted == []


def test_broker_stop_sell_uses_day_stop_request():
    class Client:
        def __init__(self):
            self.request = None

        def submit_order(self, request):
            self.request = request
            return SimpleNamespace(id="stop-42")

    client = Client()

    result = submit_stop_sell("AAPL", qty=4, stop_price=95.123, client=client)

    assert result["ok"] is True and result["id"] == "stop-42"
    assert float(client.request.qty) == 4
    assert float(client.request.stop_price) == 95.12
    assert str(client.request.side).lower().endswith("sell")
    assert str(client.request.time_in_force).lower().endswith("day")
