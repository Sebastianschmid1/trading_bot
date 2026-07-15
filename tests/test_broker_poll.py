from datetime import datetime, timedelta, timezone

import pytest

from stockbot.core import db
from stockbot.core.domain import Mode, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.execution.broker_poll import map_broker_status, poll_broker_orders
from stockbot.execution.oms import OMS


@pytest.mark.parametrize(
    ("broker_status", "oms_status", "event_type"),
    [
        ("filled", "partially_filled", "fill"),
        ("partially_filled", "accepted_by_broker", "partial_fill"),
        ("canceled", "cancel_requested", "cancelled"),
        ("expired", "accepted_by_broker", "expired"),
        ("rejected", "submitted", "rejected"),
        ("accepted", "submitted", "accepted"),
        ("new", "submitted", "accepted"),
    ],
)
def test_maps_alpaca_status_to_worker_event(broker_status, oms_status, event_type):
    events = map_broker_status(
        {"id": 1, "status": oms_status, "broker_order_id": "alpaca-1"},
        {"ok": True, "status": broker_status, "filled_qty": 1},
    )

    assert [event[0] for event in events] == [event_type]


@pytest.mark.parametrize("status", ["unknown", "pending_cancel", ""])
def test_unknown_status_does_not_create_event(status):
    assert map_broker_status(
        {"id": 1, "status": "submitted", "broker_order_id": "alpaca-1"},
        {"ok": True, "status": status},
    ) == []


def test_unchanged_status_does_not_create_event():
    assert map_broker_status(
        {"id": 1, "status": "accepted_by_broker", "broker_order_id": "alpaca-1"},
        {"ok": True, "status": "accepted"},
    ) == []


@pytest.fixture
def submitted_order(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "broker-poll.db")
    db.init_db()
    signal = Signal(
        id=17, strategy_version_id=3, ticker="AAPL", direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )
    intent = TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="telegram",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key="poll-order",
    )
    row, _ = db.create_oms_order(intent, ticker="AAPL", qty=2, notional=None)
    row = db.transition_oms_order(
        row["id"], from_status="created", to_status="validated", event_type="validated",
    )
    row = db.transition_oms_order(
        row["id"], from_status="validated", to_status="submitted", event_type="submitted",
        broker_order_id="alpaca-order-1",
    )
    oms = OMS(signal_loader=lambda signal_id: signal)
    return oms, row


def test_repeated_poll_is_deduplicated_by_real_oms(submitted_order):
    oms, order = submitted_order
    response = {"ok": True, "status": "accepted", "filled_qty": 0}

    first = poll_broker_orders(oms, [order], status_fetcher=lambda _: response)
    event_count = len(db.get_oms_order_events(order["id"]))
    second = poll_broker_orders(oms, [order], status_fetcher=lambda _: response)

    assert first[0].deduplicated is False
    assert second[0].deduplicated is True
    assert len(db.get_oms_order_events(order["id"])) == event_count
    assert sum(event["event_type"] == "accepted"
               for event in db.get_oms_order_events(order["id"])) == 1


def test_open_order_loader_returns_only_pollable_orders(submitted_order):
    _, order = submitted_order

    assert [row["id"] for row in db.get_open_oms_orders()] == [order["id"]]

    db.transition_oms_order(
        order["id"], from_status="submitted", to_status="rejected", event_type="rejected",
    )
    assert db.get_open_oms_orders() == []


def test_accepted_partial_fill_fill_sequence(submitted_order):
    oms, initial_order = submitted_order
    responses = [
        {"ok": True, "status": "accepted", "filled_qty": 0},
        {"ok": True, "status": "partially_filled", "filled_qty": 1,
         "filled_avg_price": 100},
        {"ok": True, "status": "filled", "filled_qty": 2,
         "filled_avg_price": 101},
    ]
    results = []

    for response in responses:
        order = db.get_oms_order(initial_order["id"])
        results.extend(poll_broker_orders(
            oms, [order], status_fetcher=lambda _, snapshot=response: snapshot,
            strategy_version_id=3,
        ))

    broker_events = [
        event["event_type"] for event in db.get_oms_order_events(initial_order["id"])
        if event["broker_event_id"]
    ]
    # Das bestehende OMS persistiert Worker-Aliase als kanonische Statusnamen.
    assert broker_events == ["accepted", "partially_filled", "filled"]
    assert db.get_oms_order(initial_order["id"])["status"] == OrderStatus.FILLED.value
    assert results[-1].position.qty == 2
    assert results[-1].position.avg_entry_price == 101
    assert results[-1].position.status.value == "open"


def test_broker_failure_does_not_stop_other_orders(submitted_order, caplog):
    oms, first = submitted_order
    second_intent = TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="telegram",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key="poll-order-2",
    )
    second, _ = db.create_oms_order(second_intent, ticker="AAPL", qty=2, notional=None)
    second = db.transition_oms_order(
        second["id"], from_status="created", to_status="validated", event_type="validated",
    )
    second = db.transition_oms_order(
        second["id"], from_status="validated", to_status="submitted", event_type="submitted",
        broker_order_id="alpaca-order-2",
    )

    def fetch(order_id):
        if order_id == "alpaca-order-1":
            raise ConnectionError("offline")
        return {"ok": True, "status": "accepted", "filled_qty": 0}

    results = poll_broker_orders(oms, [first, second], status_fetcher=fetch)

    assert len(results) == 1
    assert results[0].order.id == second["id"]
    assert "Broker-Poll für OMS-Order" in caplog.text
