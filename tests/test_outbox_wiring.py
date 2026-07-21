"""W4.5: Domain-Events entstehen im OMS und werden über die Outbox tatsächlich zugestellt.

Bis 2026-07-21 waren `core/events.py`, `core/outbox.py` und `core/event_consumers.py` gebaut,
aber an KEINER Stelle in `tgbot/`, `web/` oder `execution/` verwendet — es entstand nie ein
Event, nichts wurde zugestellt, und `burn_in.dead_letter_events` meldete strukturell 0. Diese
Tests halten die Verdrahtung fest.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from stockbot.core import db, events, outbox
from stockbot.core.domain import Mode, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.core.event_consumers import DedupConsumer, ObservabilityConsumer
from stockbot.execution.oms import OMS

USER = 8181


class AcceptingBroker:
    def submit_buy(self, symbol, **kwargs):
        return {"ok": True, "id": "paper-1", "status": "accepted"}


class RejectingBroker:
    def submit_buy(self, symbol, **kwargs):
        return {"ok": False, "detail": "insufficient buying power"}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "outbox.db")
    db.init_db()


def _signal():
    return Signal(id=41, strategy_version_id=1, ticker="AAPL", direction="long", mode=Mode.PAPER,
                  status=SignalStatus.ACCEPTED)


def _intent(key="k-1"):
    return TradeIntent(
        user_id=USER, signal_id=41, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="web",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key=key,
    )


def _outbox_types():
    rows = db.fetch_due_outbox_events("2999-12-31 23:59:59", limit=100)
    return [r["event_type"] for r in rows]


def test_submitted_order_produces_a_domain_event():
    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=AcceptingBroker())
    oms.submit_intent(_intent(), price=100.0, trade_size=1000.0)

    assert events.ORDER_SUBMITTED in _outbox_types()


def test_rejected_order_produces_a_rejection_event():
    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=RejectingBroker())
    oms.submit_intent(_intent("k-rej"), price=100.0, trade_size=1000.0)

    assert events.ORDER_REJECTED in _outbox_types()


def test_events_are_versioned_and_carry_a_trace_id():
    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=AcceptingBroker())
    oms.submit_intent(_intent("k-trace"), price=100.0, trade_size=1000.0)

    row = db.fetch_due_outbox_events("2999-12-31 23:59:59", limit=1)[0]
    assert row["version"] == events.EVENT_SCHEMA[row["event_type"]]
    assert row["trace_id"]                       # nie leer — Nachvollziehbarkeit


def test_scheduler_job_delivers_and_marks_events_delivered():
    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=AcceptingBroker())
    oms.submit_intent(_intent("k-deliver"), price=100.0, trade_size=1000.0)
    assert _outbox_types(), "Vorbedingung: es liegt etwas in der Outbox"

    result = outbox.deliver_due([DedupConsumer(ObservabilityConsumer(logging.getLogger("t")))])

    assert result.delivered >= 1
    assert result.dead == 0
    assert _outbox_types() == []                 # nichts bleibt fällig liegen


def test_outbox_failure_never_breaks_the_trading_path(monkeypatch, caplog):
    """Ein kaputtes Einreihen darf eine Order weder verhindern noch zurückrollen."""
    def boom(*a, **k):
        raise RuntimeError("outbox kaputt")

    monkeypatch.setattr(db, "enqueue_outbox_event", boom)
    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=AcceptingBroker())

    with caplog.at_level(logging.WARNING):
        result = oms.submit_intent(_intent("k-boom"), price=100.0, trade_size=1000.0)

    assert result.ok                                            # Order steht trotzdem
    assert db.get_oms_order(result.order.id)["status"] == OrderStatus.ACCEPTED_BY_BROKER.value
