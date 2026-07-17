"""Tests für Domain-Events + Outbox + Consumer (W4.5, Pakete B/C/D)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.core import db, events, outbox
from stockbot.core.event_consumers import (
    DedupConsumer,
    NotificationConsumer,
    render_notification,
)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", Path(tmp_path) / "outbox.db")
    db.init_db()
    return db


def _base():
    """Zeitbasis nach dem Enqueue — enqueue stempelt next_attempt_at auf die echte Uhr."""
    return datetime.now(timezone.utc)


class _Recorder:
    def __init__(self):
        self.handled = []

    def handle(self, event):
        self.handled.append(event["event_id"])


class _Failing:
    def handle(self, event):
        raise RuntimeError("Zustellung fehlgeschlagen")


class _FlakyOnce:
    def __init__(self):
        self.calls = 0

    def handle(self, event):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("erster Versuch scheitert")


# ── Paket B: Events ──────────────────────────────────────────────────────────

def test_make_event_stamps_registered_version():
    ev = events.make_event(events.ORDER_FILLED, {"ticker": "AAPL"}, trace_id="t1")
    assert ev.version == events.EVENT_SCHEMA[events.ORDER_FILLED]
    assert ev.event_type == events.ORDER_FILLED
    assert ev.trace_id == "t1"
    assert ev.event_id  # eindeutig vergeben


def test_make_event_rejects_unknown_type_and_missing_trace():
    with pytest.raises(events.UnknownEventType):
        events.make_event("Nonsense", {}, trace_id="t")
    with pytest.raises(ValueError):
        events.make_event(events.ORDER_FILLED, {}, trace_id="")


# ── Paket C: Outbox-Zustellung ───────────────────────────────────────────────

def test_enqueue_and_deliver_marks_delivered(fresh_db):
    ev = events.make_event(events.SIGNAL_GENERATED, {"ticker": "X"}, trace_id="tr")
    db.enqueue_outbox_event(ev)
    now = _base()
    rec = _Recorder()
    result = outbox.deliver_due([rec], now=now)
    assert result.delivered == 1
    assert rec.handled == [ev.event_id]
    assert db.outbox_backlog_count() == 0        # nach Zustellung kein Rückstand
    # erneute Zustellung liefert nichts mehr (delivered wird nicht neu geholt)
    assert outbox.deliver_due([rec], now=now).delivered == 0


def test_enqueue_atomic_within_transaction_rolls_back(fresh_db):
    ev = events.make_event(events.ORDER_SUBMITTED, {"ticker": "Y"}, trace_id="tr")
    try:
        with db._database().transaction() as tx:
            db.enqueue_outbox_event(ev, transaction=tx)
            raise RuntimeError("Domänenänderung scheitert → alles zurückrollen")
    except RuntimeError:
        pass
    assert db.outbox_backlog_count() == 0        # Event NICHT persistiert (atomar mit dem Abbruch)


def test_failed_delivery_retries_then_dead_letters(fresh_db):
    ev = events.make_event(events.ORDER_FILLED, {"ticker": "Z"}, trace_id="tr")
    db.enqueue_outbox_event(ev, max_attempts=2)
    fail = _Failing()
    now = _base()

    # 1. Versuch scheitert → retry, next_attempt_at in der Zukunft
    r1 = outbox.deliver_due([fail], now=now)
    assert (r1.retried, r1.dead, r1.delivered) == (1, 0, 0)
    assert db.outbox_backlog_count() == 1
    # sofort erneut (gleiche now) → noch nicht fällig, nichts passiert
    assert outbox.deliver_due([fail], now=now).retried == 0

    # 2. Versuch (weit in der Zukunft, jetzt fällig) → attempts erschöpft → dead-letter
    later = now + timedelta(hours=2)
    r2 = outbox.deliver_due([fail], now=later)
    assert (r2.dead, r2.retried) == (1, 0)
    assert db.outbox_backlog_count() == 0        # dead zählt nicht mehr als Rückstand


def test_backoff_is_exponential_and_capped():
    assert outbox.exponential_backoff(1, base=30) == 60
    assert outbox.exponential_backoff(2, base=30) == 120
    assert outbox.exponential_backoff(100, base=30) == outbox.BACKOFF_CAP_SEC


# ── Paket D: Consumer / Idempotenz / Templates ───────────────────────────────

def test_dedup_consumer_skips_seen_event_ids():
    rec = _Recorder()
    dedup = DedupConsumer(rec)
    event = {"event_id": "e1", "event_type": events.ORDER_FILLED, "payload": {}}
    dedup.handle(event)
    dedup.handle(event)                 # zweimal dasselbe Event
    assert rec.handled == ["e1"]        # inner nur einmal aufgerufen
    assert dedup.seen_count == 1


def test_idempotent_consumer_survives_retry(fresh_db):
    """Ein Consumer-Fehler retryt das ganze Event — der erfolgreiche Consumer läuft dann erneut,
    Dedup verhindert aber die doppelte Wirkung."""
    ev = events.make_event(events.ORDER_FILLED, {"ticker": "A"}, trace_id="tr")
    db.enqueue_outbox_event(ev, max_attempts=5)
    rec = DedupConsumer(_Recorder())
    flaky = _FlakyOnce()
    now = _base()
    # 1. Runde: rec erfolgreich, flaky scheitert → Retry (Event bleibt pending)
    outbox.deliver_due([rec, flaky], now=now)
    # 2. Runde (fällig): rec sieht dasselbe Event erneut (Dedup → kein Doppel), flaky nun ok
    outbox.deliver_due([rec, flaky], now=now + timedelta(hours=1))
    assert rec.seen_count == 1          # trotz zweier Zustellversuche nur einmal wirksam
    assert flaky.calls == 2
    assert db.outbox_backlog_count() == 0


def test_notification_consumer_formats_without_emojis():
    sent = []
    consumer = NotificationConsumer(sent.append)
    consumer.handle({"event_id": "e", "event_type": events.ORDER_REJECTED,
                     "payload": {"ticker": "AAPL", "reason": "Risiko", "mode": "paper"}})
    assert sent == ["[PAPER] Order abgelehnt: AAPL — Risiko"]
    ks = render_notification({"event_id": "e2", "event_type": events.KILL_SWITCH_ACTIVATED,
                              "payload": {"mode": "live"}})
    assert ks == "[LIVE] Kill-Switch AKTIV — keine neuen Positionen."
    assert "🚀" not in ks and "🔥" not in ks
