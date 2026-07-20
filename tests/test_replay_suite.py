"""W8 / Gate P10 — Replay-Suite.

Spielt die in der Checkliste (Phase 10) geforderten Broker-Event-Folgen gegen einen
echten OMS-Order-Lebenszyklus ab: normaler Fill, doppeltes Event, verspätetes Event,
Partial Fill, Fill nach Cancel, unbekannte Order, externe Position.

Der Fokus liegt bewusst auf der **Wiederholbarkeit**: dieselbe Eventfolge zweimal
abgespielt darf nie zu doppelten Orders, doppelten Positionen oder doppelten
Event-Zeilen führen (Gate P10: „keine doppelten Orders in allen Wiederholungstests").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from stockbot.core import db
from stockbot.core.domain import (
    Mode, OrderStatus, PositionStatus, Signal, SignalStatus, TradeIntent,
)
from stockbot.execution.broker_adapter import BrokerPosition
from stockbot.execution.broker_event_worker import process_broker_event
from stockbot.execution.oms import OMS
from stockbot.execution.reconciliation import reconcile_positions

USER = 4242
STRATEGY_VERSION = 3


class AcceptingBroker:
    """Broker, der jede Order annimmt und nie von sich aus weiterläuft."""

    def submit_buy(self, symbol, **kwargs):
        return {"ok": True, "id": f"paper-{symbol}", "status": "accepted"}


def _signal() -> Signal:
    return Signal(
        id=17, strategy_version_id=STRATEGY_VERSION, ticker="AAPL", direction="long",
        mode=Mode.PAPER, status=SignalStatus.ACCEPTED,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )


def _intent(key: str) -> TradeIntent:
    return TradeIntent(
        user_id=USER, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="telegram",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key=key,
    )


@pytest.fixture
def order(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "replay.db")
    db.init_db()
    oms = OMS(signal_loader=lambda signal_id: _signal(), broker_client=AcceptingBroker())
    result = oms.submit_intent(_intent("replay-order"), price=100.0, trade_size=1000.0)
    assert result.order.status == OrderStatus.ACCEPTED_BY_BROKER
    return oms, result.order


def _replay(oms, order, events, *, position=None):
    """Spielt eine Eventfolge ab und gibt das letzte Ergebnis zurück."""
    last = None
    for event_type, event_id, payload in events:
        last = process_broker_event(
            oms, order_id=order.id, event_type=event_type, broker_event_id=event_id,
            payload=payload, strategy_version_id=STRATEGY_VERSION,
            existing_position=position,
        )
        position = last.position if last.position is not None else position
    return last


# ── 1. Normaler Fill ─────────────────────────────────────────────────────────

def test_replay_normal_fill_opens_exactly_one_position(order):
    oms, o = order
    result = _replay(oms, o, [("fill", "f1", {"filled_qty": 10, "filled_avg_price": 101.0})])

    assert result.order.status == OrderStatus.FILLED
    assert result.position.status == PositionStatus.OPEN
    assert result.position.qty == 10
    # created → validated → submitted → accepted → filled, genau einmal
    assert [e["event_type"] for e in db.get_oms_order_events(o.id)][-1] == "filled"
    assert len(db.get_oms_order_events(o.id)) == 5


# ── 2. Event doppelt ─────────────────────────────────────────────────────────

def test_replay_of_the_same_event_sequence_is_idempotent(order):
    oms, o = order
    sequence = [
        ("partial_fill", "p1", {"filled_qty": 4, "filled_avg_price": 100.0}),
        ("fill", "f1", {"filled_qty": 10, "filled_avg_price": 100.5}),
    ]
    first = _replay(oms, o, sequence)
    events_after_first = len(db.get_oms_order_events(o.id))

    second = _replay(oms, o, sequence, position=first.position)

    assert second.deduplicated is True
    assert second.order.status == OrderStatus.FILLED
    assert second.position == first.position                      # keine zweite Position
    assert len(db.get_oms_order_events(o.id)) == events_after_first   # keine doppelte Zeile


# ── 3. Verspätetes/Out-of-order-Event ────────────────────────────────────────

def test_late_partial_fill_after_full_fill_is_rejected_without_corrupting_state(order):
    oms, o = order
    filled = _replay(oms, o, [("fill", "f1", {"filled_qty": 10, "filled_avg_price": 100.0})])
    events_before = len(db.get_oms_order_events(o.id))

    # Der Broker liefert den früheren Partial Fill verspätet nach — die Zustandsmaschine
    # lehnt den Rückschritt laut ab, statt die Order stillschweigend zurückzudrehen.
    with pytest.raises(ValueError, match="filled -> partially_filled"):
        _replay(oms, o, [("partial_fill", "p-late", {"filled_qty": 4, "filled_avg_price": 99.0})],
                position=filled.position)

    assert db.get_oms_order(o.id)["status"] == OrderStatus.FILLED.value
    assert len(db.get_oms_order_events(o.id)) == events_before   # nichts geschrieben


# ── 4. Partial Fill ──────────────────────────────────────────────────────────

def test_partial_fill_leaves_remaining_quantity_and_pending_position(order):
    oms, o = order
    result = _replay(oms, o, [("partial_fill", "p1", {"filled_qty": 3, "filled_avg_price": 100.0})])

    assert result.order.status == OrderStatus.PARTIALLY_FILLED
    assert result.position.status == PositionStatus.PENDING_OPEN
    assert result.remaining_qty == pytest.approx(7.0)


# ── 5. Fill nach Cancel ──────────────────────────────────────────────────────

def test_fill_after_cancel_is_refused_and_surfaces_via_reconciliation(order):
    oms, o = order
    _replay(oms, o, [("cancelled", "c1", {"detail": "user cancel"})])
    assert db.get_oms_order(o.id)["status"] == OrderStatus.CANCELLED.value
    events_before = len(db.get_oms_order_events(o.id))

    # Ein Fill, der den Cancel überholt hat, darf die stornierte Order NICHT wiederbeleben.
    with pytest.raises(ValueError, match="cancelled -> filled"):
        _replay(oms, o, [("fill", "f-after-cancel", {"filled_qty": 10, "filled_avg_price": 100.0})])

    assert db.get_oms_order(o.id)["status"] == OrderStatus.CANCELLED.value
    assert len(db.get_oms_order_events(o.id)) == events_before

    # Die dadurch entstandene Broker-Position bleibt trotzdem sichtbar: die
    # Reconciliation meldet sie als unbekannte Position statt sie zu verschweigen.
    findings = reconcile_positions([], [BrokerPosition(symbol="AAPL", qty=10.0, avg_entry_price=100.0)])
    assert [(f.kind, f.symbol) for f in findings] == [("unknown_broker_position", "AAPL")]


# ── 6. Unbekannte Order ──────────────────────────────────────────────────────

def test_event_for_unknown_order_raises_instead_of_inventing_state(order):
    oms, _ = order
    with pytest.raises(KeyError):
        process_broker_event(
            oms, order_id=999_999, event_type="fill", broker_event_id="ghost",
            payload={"filled_qty": 1}, strategy_version_id=STRATEGY_VERSION,
        )


# ── 7. Externe Position ──────────────────────────────────────────────────────

def test_external_broker_position_shows_up_as_reconciliation_finding(order):
    oms, o = order
    result = _replay(oms, o, [("fill", "f1", {"filled_qty": 10, "filled_avg_price": 100.0})])

    findings = reconcile_positions(
        [result.position],
        [BrokerPosition(symbol="AAPL", qty=10.0, avg_entry_price=100.0), BrokerPosition(symbol="TSLA", qty=5.0, avg_entry_price=200.0)],
    )

    kinds = {(f.kind, f.symbol) for f in findings}
    assert ("unknown_broker_position", "TSLA") in kinds
    assert not any(f.symbol == "AAPL" for f in findings)   # bekannte Position ist sauber


# ── Audit-Kontext-Cache (Folgeaufgabe aus W1.4) ──────────────────────────────

def test_audit_context_cache_is_released_when_the_order_becomes_terminal(order):
    """Der Cache im langlebigen OMS-Singleton darf nicht unbegrenzt wachsen."""
    oms, o = order
    assert o.id in oms._audit_contexts          # waehrend der Order-Laufzeit gehalten

    _replay(oms, o, [("fill", "f1", {"filled_qty": 10, "filled_avg_price": 100.0})])

    assert oms._audit_contexts == {}


def test_audit_context_is_reloaded_from_persistence_after_eviction(order):
    """Nach dem Freigeben bleibt das Audit vollstaendig — der Cache ist read-through."""
    oms, o = order
    events = []
    oms.audit_sink = events.append
    _replay(oms, o, [("cancelled", "c1", {"detail": "user cancel"})])
    assert oms._audit_contexts == {}

    # Ein spaeteres Audit zum selben Order laedt den Intent wieder aus der Persistenz
    # und behaelt Actor und Quelle bei, statt auf den Fallback zurueckzufallen.
    oms._audit_transition(o, OrderStatus.CANCELLED, OrderStatus.CANCELLED, {})

    assert events[-1].actor == f"user:{USER}"
    assert events[-1].source_channel == "telegram"
