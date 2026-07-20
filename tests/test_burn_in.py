"""W8/Gate P10 — Auswertung des Paper-Burn-ins aus den vorhandenen Tabellen."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from stockbot.core import burn_in, db
from stockbot.core.domain import Mode, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.core.events import DomainEvent
from stockbot.execution.oms import OMS

USER = 6161
WINDOW = ("2000-01-01 00:00:00", "2999-12-31 23:59:59")


class AcceptingBroker:
    def submit_buy(self, symbol, **kwargs):
        return {"ok": True, "id": "paper-1", "status": "accepted"}


class RejectingBroker:
    def submit_buy(self, symbol, **kwargs):
        return {"ok": False, "detail": "insufficient buying power"}


def _signal():
    return Signal(
        id=31, strategy_version_id=1, ticker="AAPL", direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )


def _intent(key):
    return TradeIntent(
        user_id=USER, signal_id=31, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="web",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key=key,
    )


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "burnin.db")
    db.init_db()


def _report(**kwargs):
    return burn_in.build_burn_in_report(*WINDOW, **kwargs)


def test_empty_window_has_no_error_rate_instead_of_a_fake_zero():
    report = _report()
    assert report.orders_submitted == 0
    assert report.error_rate is None          # kein erfundener Nullwert
    assert report.clean is True


def test_error_rate_counts_broker_rejections():
    OMS(signal_loader=lambda sid: _signal(), broker_client=AcceptingBroker()).submit_intent(
        _intent("ok-1"), price=100.0, trade_size=1000.0)
    rejected = OMS(signal_loader=lambda sid: _signal(), broker_client=RejectingBroker()).submit_intent(
        _intent("bad-1"), price=100.0, trade_size=1000.0)
    assert rejected.order.status == OrderStatus.REJECTED

    report = _report()
    assert (report.orders_submitted, report.orders_failed) == (2, 1)
    assert report.error_rate == pytest.approx(0.5)
    assert report.clean is True                # Ablehnung ist kein Gate-Verstoß


def test_idempotent_retry_never_shows_up_as_a_duplicate_order():
    oms = OMS(signal_loader=lambda sid: _signal(), broker_client=AcceptingBroker())
    oms.submit_intent(_intent("same"), price=100.0, trade_size=1000.0)
    oms.submit_intent(_intent("same"), price=100.0, trade_size=1000.0)

    report = _report()
    assert report.orders_submitted == 1
    assert report.duplicate_orders == 0


def test_dead_letter_events_break_the_gate():
    db.enqueue_outbox_event(DomainEvent(event_type="order.filled", version=1, payload={},
                                        trace_id="t", event_id="evt-dead"), max_attempts=1)
    db.mark_outbox_dead("evt-dead", "consumer offline")

    report = _report()
    assert report.dead_letter_events == 1
    assert report.clean is False


def test_open_reconciliation_findings_break_the_gate():
    report = _report(reconciliation_findings=2)
    assert report.clean is False
    assert "NICHT sauber" in burn_in.format_burn_in_report(report)


def test_report_formats_a_signable_summary():
    text = burn_in.format_burn_in_report(_report())
    assert "Paper-Burn-in" in text
    assert "Fehlerquote —" in text             # ohne Datenlage kein Prozentwert
    assert "Gate P10: sauber" in text


def test_datetime_window_is_bound_as_naive_utc_text():
    """`created_at` ist auch unter Postgres TEXT — datetime-Parameter sprengen den Vergleich.

    Der Report wird von Menschen/Jobs mit `datetime.now(timezone.utc)` aufgerufen; ohne
    Normalisierung schlug das auf dem VPS mit `operator does not exist: text >= timestamp
    with time zone` fehl, während SQLite still verglich.
    """
    seen = {}

    class Recorder:
        def burn_in_order_stats(self, since, until):
            seen["since"], seen["until"] = since, until
            return {"submitted": 0, "failed": 0, "duplicate_orders": 0,
                    "duplicate_broker_events": 0, "dead_letter_events": 0}

    since = datetime(2026, 7, 20, 12, 30, 45, tzinfo=timezone.utc)
    until = datetime(2026, 7, 20, 16, 0, 0, tzinfo=timezone(timedelta(hours=2)))  # 14:00 UTC

    report = burn_in.build_burn_in_report(since, until, persistence=Recorder())

    assert seen == {"since": "2026-07-20 12:30:45", "until": "2026-07-20 14:00:00"}
    assert all(isinstance(v, str) for v in seen.values())
    assert (report.since, report.until) == (seen["since"], seen["until"])
