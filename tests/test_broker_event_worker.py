from datetime import datetime, timedelta, timezone

import pytest

from stockbot.core import db
from stockbot.core.audit_log import AuditLog
from stockbot.core.domain import Mode, OrderStatus, PositionStatus, Signal, SignalStatus, TradeIntent
from stockbot.execution.broker_event_worker import process_broker_event
from stockbot.execution.oms import OMS


class AcceptingBroker:
    def submit_buy(self, symbol, **kwargs):
        return {"ok": True, "id": "paper-original", "status": "accepted"}


@pytest.fixture
def accepted_order(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "broker-events.db")
    db.init_db()
    signal = Signal(
        id=17, strategy_version_id=3, ticker="AAPL", direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )
    intent = TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="telegram",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key="event-worker-order",
    )
    oms = OMS(signal_loader=lambda signal_id: signal, broker_client=AcceptingBroker())
    submitted = oms.submit_intent(intent, price=100.0, trade_size=1000.0)
    assert submitted.order.status == OrderStatus.ACCEPTED_BY_BROKER
    return oms, submitted.order


def test_fill_projects_open_position_and_persists_event(accepted_order):
    oms, order = accepted_order

    result = process_broker_event(
        oms, order_id=order.id, event_type="fill", broker_event_id="fill-1",
        payload={"filled_qty": 10, "filled_avg_price": 101.25}, strategy_version_id=3,
    )

    assert result.order.status == OrderStatus.FILLED
    assert result.position.status == PositionStatus.OPEN
    assert result.position.qty == 10
    assert result.position.avg_entry_price == 101.25
    assert result.remaining_qty == 0
    assert db.get_oms_order_events(order.id)[-1]["broker_event_id"] == "fill-1"


def test_duplicate_event_is_side_effect_free(accepted_order):
    oms, order = accepted_order
    notifications = []
    first = process_broker_event(
        oms, order_id=order.id, event_type="partial_fill", broker_event_id="partial-1",
        payload={"filled_qty": 4, "filled_avg_price": 99}, strategy_version_id=3,
        notifier=notifications.append,
    )
    event_count = len(db.get_oms_order_events(order.id))

    duplicate = process_broker_event(
        oms, order_id=order.id, event_type="partial_fill", broker_event_id="partial-1",
        payload={"filled_qty": 8, "filled_avg_price": 105},
        existing_position=first.position, notifier=notifications.append,
    )

    assert duplicate.deduplicated is True
    assert duplicate.order.status == OrderStatus.PARTIALLY_FILLED
    assert duplicate.position == first.position
    assert len(db.get_oms_order_events(order.id)) == event_count
    assert notifications == [first]


def test_partial_fill_projects_pending_position_with_remaining_quantity(accepted_order):
    oms, order = accepted_order

    result = process_broker_event(
        oms, order_id=order.id, event_type="partial_fill", broker_event_id="partial-2",
        payload={"filled_qty": "3.5", "filled_avg_price": "100.5"},
        strategy_version_id=3,
    )

    assert result.order.status == OrderStatus.PARTIALLY_FILLED
    assert result.position.status == PositionStatus.PENDING_OPEN
    assert result.position.qty == 3.5
    assert result.remaining_qty == 6.5


def test_invalid_fill_payload_is_not_persisted(accepted_order):
    oms, order = accepted_order
    event_count = len(db.get_oms_order_events(order.id))

    with pytest.raises(ValueError, match="Fill payload requires"):
        process_broker_event(
            oms, order_id=order.id, event_type="partial_fill", broker_event_id="invalid-fill",
            payload={}, strategy_version_id=3,
        )

    assert db.get_oms_order(order.id)["status"] == "accepted_by_broker"
    assert len(db.get_oms_order_events(order.id)) == event_count


@pytest.mark.parametrize(
    ("event_type", "expected_status"),
    [("rejected", OrderStatus.REJECTED), ("cancelled", OrderStatus.CANCELLED)],
)
def test_non_fill_terminal_events_do_not_project_position(
    accepted_order, event_type, expected_status,
):
    oms, order = accepted_order

    result = process_broker_event(
        oms, order_id=order.id, event_type=event_type,
        broker_event_id=f"{event_type}-1", payload={"detail": "broker response"},
    )

    assert result.order.status == expected_status
    assert result.position is None
    assert result.remaining_qty is None


def test_success_records_audit_event(accepted_order):
    oms, order = accepted_order
    audit = AuditLog()

    result = process_broker_event(
        oms, order_id=order.id, event_type="partial_fill", broker_event_id="audit-1",
        payload={"filled_qty": 2}, strategy_version_id=3, audit_log=audit,
    )

    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.actor == "broker"
    assert event.entity_type == "order" and event.entity_id == str(order.id)
    assert event.action == "partial_fill"
    assert event.old_state == "accepted_by_broker"
    assert event.new_state == "partially_filled"
    assert event.trace_id == "audit-1"
    assert event.source_channel == "broker_event_worker"
    assert event.user_id == 42
    assert result.position is not None


def test_notifier_receives_worker_result(accepted_order):
    oms, order = accepted_order
    notifications = []

    result = process_broker_event(
        oms, order_id=order.id, event_type="fill", broker_event_id="notify-1",
        payload={"filled_qty": 10}, strategy_version_id=3,
        notifier=notifications.append,
    )

    assert notifications == [result]


def test_replaced_event_is_persisted_without_inventing_an_order_status(accepted_order):
    oms, order = accepted_order

    result = process_broker_event(
        oms, order_id=order.id, event_type="replaced", broker_event_id="replace-1",
        payload={"broker_order_id": "paper-replacement"},
    )

    assert result.order.status == OrderStatus.ACCEPTED_BY_BROKER
    assert result.order.broker_order_id == "paper-replacement"
    assert result.position is None
    event = db.get_oms_order_events(order.id)[-1]
    assert event["event_type"] == "replaced"
    assert event["from_status"] == event["to_status"] == "accepted_by_broker"
    assert event["broker_event_id"] == "replace-1"


@pytest.mark.parametrize(
    ("event_type", "expected_status"),
    [("accepted", OrderStatus.ACCEPTED_BY_BROKER), ("expired", OrderStatus.EXPIRED)],
)
def test_remaining_supported_events_are_persisted(
    accepted_order, event_type, expected_status,
):
    oms, order = accepted_order

    result = process_broker_event(
        oms, order_id=order.id, event_type=event_type,
        broker_event_id=f"{event_type}-event", payload={},
    )

    assert result.order.status == expected_status
    event = db.get_oms_order_events(order.id)[-1]
    assert event["event_type"] == event_type
    assert event["broker_event_id"] == f"{event_type}-event"
