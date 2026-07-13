from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from stockbot.core import db, risk
from stockbot.core.domain import Mode, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.execution.oms import OMS


class FakeBroker:
    def __init__(self):
        self.submissions = []

    def submit_buy(self, symbol, **kwargs):
        self.submissions.append((symbol, kwargs))
        return {"ok": True, "id": "paper-123", "status": "filled"}


@pytest.fixture
def oms_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "oms.db")
    db.init_db()


def _signal() -> Signal:
    return Signal(
        id=17, strategy_version_id=3, ticker="AAPL", direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )


def _intent(key="telegram:42:17") -> TradeIntent:
    return TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="telegram",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key=key,
    )


def test_happy_path_persists_state_machine_and_broker_id(oms_db):
    broker = FakeBroker()
    notifications = []
    service = OMS(signal_loader=lambda signal_id: _signal(), broker_client=broker,
                  notifier=notifications.append)

    result = service.submit_intent(_intent(), price=200.0, trade_size=1000.0)

    assert result.ok is True
    assert result.order.status == OrderStatus.FILLED
    assert result.order.mode is _signal().mode
    assert result.order.broker_order_id == "paper-123"
    assert result.order.client_order_id == f"oms-{result.order.id}"
    assert broker.submissions[0][1]["client_order_id"] == result.order.client_order_id
    assert [event["to_status"] for event in db.get_oms_order_events(result.order.id)] == [
        "created", "validated", "submitted", "accepted_by_broker", "filled",
    ]
    assert notifications == [result]


def test_intent_mode_must_match_signal(oms_db):
    intent = replace(_intent(), mode=Mode.SHADOW)

    result = OMS(signal_loader=lambda signal_id: _signal(), broker_client=FakeBroker()).submit_intent(
        intent, price=200.0, trade_size=1000.0,
    )

    assert result.ok is False
    assert result.code == "mode_mismatch"


def test_risk_rejection_creates_no_order(oms_db):
    broker = FakeBroker()
    calls = []

    def reject(**kwargs):
        calls.append(kwargs)
        return risk.RiskDecision(False, "Tageslimit erreicht.", "daily_loss_limit")

    result = OMS(signal_loader=lambda signal_id: _signal(), broker_client=broker,
                 risk_checker=reject).submit_intent(
        _intent(), price=200.0, trade_size=1000.0
    )

    assert result.ok is False and result.code == "daily_loss_limit"
    assert result.order is None and len(calls) == 1 and broker.submissions == []
    assert db.get_order_by_idempotency_key(_intent().idempotency_key) is None


def test_same_idempotency_key_returns_same_order_once(oms_db):
    broker = FakeBroker()
    service = OMS(signal_loader=lambda signal_id: _signal(), broker_client=broker)
    intent = _intent()

    first = service.submit_intent(intent, price=200.0, trade_size=1000.0)
    second = service.submit_intent(intent, price=999.0, trade_size=9999.0)

    assert first.order == second.order
    assert first.idempotent is False and second.idempotent is True
    assert len(broker.submissions) == 1
    with sqlite3.connect(db.DB_FILE) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO orders
                   (trade_intent_id, user_id, ticker, side, status, idempotency_key)
                   VALUES (1, 42, 'MSFT', 'buy', 'created', ?)""",
                (intent.idempotency_key,),
            )
