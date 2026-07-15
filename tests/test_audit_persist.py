from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from stockbot.core import db
from stockbot.core.domain import AuditEvent, Mode, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.execution.oms import OMS


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "audit.db")
    db.init_db()


def _event(event_id: str, timestamp: str = "2026-07-15 10:11:12") -> AuditEvent:
    return AuditEvent(
        event_id=event_id, timestamp=timestamp, user_id=42, actor="user:42",
        entity_type="order", entity_id="7", action="state_transition",
        old_state="validated", new_state="submitted", trace_id="telegram:42:17",
        source_channel="telegram", metadata={"broker": {"status": "new"}},
    )


def test_append_roundtrip_preserves_all_audit_fields(audit_db):
    event = _event("audit-1")

    db.append_audit_event(event)

    assert db.audit_events_for_entity("order", "7") == [event]
    assert db.all_audit_events() == [event]


def test_audit_api_is_append_only_and_preserves_insertion_order(audit_db):
    first = _event("audit-1", "2026-07-15 10:11:12")
    second = _event("audit-2", "2026-07-15 10:11:12")

    db.append_audit_event(first)
    db.append_audit_event(second)

    assert db.all_audit_events() == [first, second]
    assert not hasattr(db, "update_audit_event")
    assert not hasattr(db, "delete_audit_event")


class AcceptingBroker:
    def submit_buy(self, symbol, **kwargs):
        return {"ok": True, "id": "paper-audit", "status": "accepted"}


def _signal() -> Signal:
    return Signal(
        id=17, strategy_version_id=3, ticker="AAPL", direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )


def test_oms_submit_emits_transition_audits_with_intent_context(audit_db):
    events = []
    intent = TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="telegram",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key="telegram:42:17",
    )
    oms = OMS(signal_loader=lambda signal_id: _signal(), broker_client=AcceptingBroker(),
              audit_sink=events.append)

    result = oms.submit_intent(intent, price=200.0, trade_size=1000.0)

    assert result.ok is True
    assert [event.new_state for event in events] == [
        OrderStatus.VALIDATED.value, OrderStatus.SUBMITTED.value,
        OrderStatus.ACCEPTED_BY_BROKER.value,
    ]
    assert {event.trace_id for event in events} == {intent.idempotency_key}
    assert {event.entity_id for event in events} == {str(result.order.id)}
    assert {event.source_channel for event in events} == {intent.source_channel}


def test_audit_sink_failure_does_not_break_order(audit_db):
    """Fail-open: ein werfender Audit-Sink darf die Order-Submission nicht brechen."""
    def failing_sink(_event):
        raise RuntimeError("audit db down")

    intent = TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="telegram",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key="telegram:42:99",
    )
    oms = OMS(signal_loader=lambda signal_id: _signal(), broker_client=AcceptingBroker(),
              audit_sink=failing_sink)

    result = oms.submit_intent(intent, price=200.0, trade_size=1000.0)

    assert result.ok is True


def test_timestamp_contract_normalizes_aware_values_on_write_and_read(audit_db):
    db.append_audit_event(_event("aware-write", "2026-07-15T12:11:12+02:00"))

    written = db.all_audit_events()[0]
    assert written.timestamp == "2026-07-15 10:11:12"
    assert datetime.fromisoformat(written.timestamp).tzinfo is None

    with sqlite3.connect(db.DB_FILE) as connection:
        connection.execute(
            """INSERT INTO audit_events
               (event_id, timestamp, user_id, actor, entity_type, entity_id, action,
                old_state, new_state, trace_id, source_channel, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("postgres-aware", "2026-07-15 10:11:12+00", 42, "user:42", "order", "8",
             "state_transition", "submitted", "accepted_by_broker", "trace-2",
             "telegram", "{}"),
        )

    assert db.all_audit_events()[1].timestamp == "2026-07-15 10:11:12"
