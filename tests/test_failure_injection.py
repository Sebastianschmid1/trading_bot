"""W8 / Gate P10 — Failure-Injection-Suite.

Injiziert die in der Checkliste (Phase 10) geforderten Störungen und prüft die eine
Zusage aus Gate P10: **keine unkontrollierte Order bei Feed- oder Brokerfehler**, keine
doppelten Orders, kein stillschweigend erfundener Zustand.

Abgedeckt: Feed offline, Broker-Timeout, DB-Unterbrechung, Queue-Retry, Worker-Neustart,
doppelter Callback/Request, Clock-Skew, veraltete Marktdaten.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from stockbot.core import db, outbox
from stockbot.core.data_quality import check_quote_age
from stockbot.core.events import DomainEvent
from stockbot.core.market_data import Quote
from stockbot.core.domain import Mode, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.execution.oms import OMS
from stockbot.web.api_idempotency import IdempotencyStore

USER = 5150


class BrokerTimeout(Exception):
    """Simuliert einen Netzwerk-/Timeout-Fehler beim Broker."""


def _signal() -> Signal:
    return Signal(
        id=21, strategy_version_id=1, ticker="AAPL", direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )


def _intent(key: str) -> TradeIntent:
    return TradeIntent(
        user_id=USER, signal_id=21, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="web",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key=key,
    )


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "failure.db")
    db.init_db()


def _quote(age_seconds: float) -> Quote:
    now = datetime.now(timezone.utc)
    return Quote(ticker="AAPL", price=100.0, as_of=now - timedelta(seconds=age_seconds),
                 fetched_at=now, provider="test")


# ── Feed offline ─────────────────────────────────────────────────────────────

def test_feed_offline_blocks_the_order_instead_of_guessing_a_price():
    """Ohne Kurs darf keine Order rausgehen — der OMS lehnt ab, statt zu raten."""
    submitted = []

    class Broker:
        def submit_buy(self, symbol, **kwargs):
            submitted.append(symbol)
            return {"ok": True, "id": "x", "status": "accepted"}

    def dead_feed(intent, signal):
        return {}                       # Feed liefert nichts

    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=Broker(),
              context_loader=dead_feed)
    result = oms.submit_intent(_intent("feed-offline"), trade_size=1000.0)

    assert result.ok is False
    assert result.code == "order_context_missing"
    assert submitted == []              # keine unkontrollierte Order


# ── Broker-Timeout ───────────────────────────────────────────────────────────

def test_broker_timeout_leaves_the_order_rejected_and_never_duplicates_it():
    calls = []

    class TimingOutBroker:
        def submit_buy(self, symbol, **kwargs):
            calls.append(symbol)
            raise BrokerTimeout("connection timed out")

    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=TimingOutBroker())
    result = oms.submit_intent(_intent("broker-timeout"), price=100.0, trade_size=1000.0)

    assert result.ok is False
    assert result.code == "broker_rejected"
    assert result.order.status == OrderStatus.REJECTED
    assert len(calls) == 1              # genau ein Versuch, kein blinder Retry

    # Der Retry mit demselben Idempotency-Key legt KEINE zweite Order an.
    retry = oms.submit_intent(_intent("broker-timeout"), price=100.0, trade_size=1000.0)
    assert retry.order.id == result.order.id
    assert len(calls) == 1


# ── DB-Unterbrechung ─────────────────────────────────────────────────────────

def test_db_failure_during_order_creation_does_not_reach_the_broker():
    submitted = []

    class Broker:
        def submit_buy(self, symbol, **kwargs):
            submitted.append(symbol)
            return {"ok": True, "id": "x", "status": "accepted"}

    class BrokenPersistence:
        def get_order_by_idempotency_key(self, key):
            return None

        def create_oms_order(self, *args, **kwargs):
            raise RuntimeError("database is locked")

    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=Broker(),
              persistence=BrokenPersistence())

    with pytest.raises(RuntimeError, match="database is locked"):
        oms.submit_intent(_intent("db-down"), price=100.0, trade_size=1000.0)

    assert submitted == []              # Order-Zeile fehlt → Broker sieht nichts


# ── Queue-Retry + Dead-Letter ────────────────────────────────────────────────

class FlakyConsumer:
    """Scheitert die ersten `fail_times` Zustellungen, danach erfolgreich."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.seen = []

    def handle(self, event):
        self.seen.append(event["event_id"])
        if len(self.seen) <= self.fail_times:
            raise RuntimeError("consumer offline")


def _enqueue(event_id="evt-1"):
    db.enqueue_outbox_event(DomainEvent(
        event_type="order.filled", version=1, payload={"ticker": "AAPL"},
        trace_id="trace-1", event_id=event_id,
    ), max_attempts=3)


def test_queue_retry_backs_off_and_eventually_delivers():
    _enqueue()
    consumer = FlakyConsumer(fail_times=1)
    now = datetime.now(timezone.utc)

    first = outbox.deliver_due([consumer], now=now)
    assert (first.retried, first.delivered) == (1, 0)
    assert outbox.backlog_count() == 1

    # Vor Ablauf des Backoffs passiert nichts — kein Hot-Loop.
    assert outbox.deliver_due([consumer], now=now).delivered == 0

    later = outbox.deliver_due([consumer], now=now + timedelta(hours=2))
    assert later.delivered == 1
    assert outbox.backlog_count() == 0


def test_queue_dead_letters_after_the_attempt_budget_instead_of_looping_forever():
    _enqueue("evt-dead")
    consumer = FlakyConsumer(fail_times=99)
    now = datetime.now(timezone.utc)

    for offset in (0, 2, 4):
        result = outbox.deliver_due([consumer], now=now + timedelta(hours=offset))
    assert result.dead == 1
    assert outbox.backlog_count() == 0   # nicht mehr fällig, aber dokumentiert


# ── Worker-Neustart ──────────────────────────────────────────────────────────

def test_worker_restart_redelivers_only_undelivered_events():
    _enqueue("evt-a")
    _enqueue("evt-b")
    ok = FlakyConsumer(fail_times=0)
    now = datetime.now(timezone.utc)

    outbox.deliver_due([ok], now=now, limit=1)      # Worker stirbt nach einem Event
    delivered_before_crash = list(ok.seen)

    fresh_worker = FlakyConsumer(fail_times=0)      # „Neustart": neuer Consumer-Zustand
    outbox.deliver_due([fresh_worker], now=now)

    assert len(delivered_before_crash) == 1
    assert set(fresh_worker.seen).isdisjoint(delivered_before_crash)
    assert outbox.backlog_count() == 0


# ── Doppelter Callback / doppelter Request ───────────────────────────────────

def test_duplicate_request_with_the_same_idempotency_key_runs_exactly_once():
    store = IdempotencyStore()
    runs = []

    def action():
        runs.append(1)
        return {"order_id": 7}

    first, replayed_first = store.get_or_run(USER, "same-key", action)
    second, replayed_second = store.get_or_run(USER, "same-key", action)

    assert (replayed_first, replayed_second) == (False, True)
    assert first == second == {"order_id": 7}
    assert len(runs) == 1


def test_duplicate_intent_submission_reuses_the_first_order():
    class Broker:
        def __init__(self):
            self.calls = 0

        def submit_buy(self, symbol, **kwargs):
            self.calls += 1
            return {"ok": True, "id": "paper-1", "status": "accepted"}

    broker = Broker()
    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=broker)
    first = oms.submit_intent(_intent("double-click"), price=100.0, trade_size=1000.0)
    second = oms.submit_intent(_intent("double-click"), price=100.0, trade_size=1000.0)

    assert first.order.id == second.order.id
    assert broker.calls == 1


# ── Clock-Skew ───────────────────────────────────────────────────────────────

def test_clock_skew_into_the_future_does_not_make_a_stale_quote_look_fresh():
    """Eine in die Zukunft laufende Uhr darf einen alten Quote nicht „verjüngen"."""
    stale = _quote(age_seconds=600)
    skewed_now = datetime.now(timezone.utc) + timedelta(minutes=30)

    decision = check_quote_age(stale, max_age_seconds=60, now=skewed_now)

    assert decision.ok is False
    assert decision.code == "quote_stale"


def test_quote_from_the_future_is_not_treated_as_expired():
    """Negativer Skew (Quote-Zeitstempel vor der lokalen Uhr) blockt nicht fälschlich."""
    future = _quote(age_seconds=-5)
    assert check_quote_age(future, max_age_seconds=60).ok is True


# ── Veraltete Marktdaten ─────────────────────────────────────────────────────

def test_stale_market_data_blocks_the_order_before_it_reaches_the_broker():
    submitted = []

    class Broker:
        def submit_buy(self, symbol, **kwargs):
            submitted.append(symbol)
            return {"ok": True, "id": "x", "status": "accepted"}

    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=Broker())
    result = oms.submit_intent(
        _intent("stale-quote"), price=100.0, trade_size=1000.0,
        risk_context={"quote": _quote(age_seconds=900), "max_quote_age_seconds": 60},
    )

    assert result.ok is False
    assert result.code == "quote_stale"
    assert submitted == []
