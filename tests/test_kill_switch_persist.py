"""Persistenz-, OMS- und UI-Verträge des Kill-Switches (W1.3)."""

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from stockbot.core import db
from stockbot.core.domain import Mode, Signal, SignalStatus, TradeIntent
from stockbot.core.risk import RiskDecision
from stockbot.core.kill_switch import KillSwitchService
from stockbot.execution.oms import OrderManagementSystem


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", Path(tmp_path) / "killswitch.db")
    db.init_db()
    return db


def test_persistence_roundtrip_restart_and_user_scope(fresh_db):
    service = KillSwitchService(persistence=fresh_db)
    activated = service.activate_global(reason="Volatilität", activated_by="admin:1")
    assert fresh_db.get_active_kill_switches()[0]["id"] == activated.id

    restarted = KillSwitchService(persistence=fresh_db)
    assert restarted.global_status is not None
    assert restarted.is_new_position_allowed(999) is False
    restarted.deactivate_global(deactivated_by="admin:1")
    assert fresh_db.get_active_kill_switches() == []

    restarted.activate_user(7, reason="Pause", activated_by="user:7")
    assert restarted.is_new_position_allowed(7) is False
    assert restarted.is_new_position_allowed(8) is True
    assert restarted.is_protective_exit_allowed(7) is True


def test_timestamp_contract_normalises_aware_values(fresh_db):
    fresh_db.activate_kill_switch(
        scope="global", user_id=None, reason="Test", activated_by="admin",
        activated_at="2026-07-16T12:34:56+00:00",
    )
    value = fresh_db.get_active_kill_switches()[0]["activated_at"]
    assert value == "2026-07-16 12:34:56"
    assert datetime.fromisoformat(value).tzinfo is None


class _Persistence:
    def get_order_by_idempotency_key(self, key): return None
    def create_oms_order(self, intent, **kwargs):
        return ({"id": 1, "trade_intent_id": 1, "user_id": intent.user_id,
                 "ticker": "AAPL", "side": "buy", "qty": 1.0, "notional": 100.0,
                 "limit_price": None, "status": "created", "broker_order_id": None,
                 "client_order_id": "cid", "idempotency_key": intent.idempotency_key,
                 "created_at": "2026-07-16 12:00:00", "updated_at": "2026-07-16 12:00:00"}, True)
    def transition_oms_order(self, order_id, from_status, to_status, **kwargs):
        row = self.create_oms_order(SimpleNamespace(user_id=5, idempotency_key="key"))[0]
        row["status"] = to_status
        row.update(kwargs)
        return row


class _Broker:
    def __init__(self): self.submissions = []
    def submit_buy(self, ticker, **kwargs):
        self.submissions.append(ticker)
        return {"ok": True, "id": "broker-1", "status": "filled"}


def _intent():
    return TradeIntent(user_id=5, signal_id=9, requested_action="accept",
                       accepted_exit_policy="default", source_channel="web",
                       created_at="2026-07-16 12:00:00", idempotency_key="key")


def _oms(checker, broker):
    signal = Signal(id=9, strategy_version_id=1, ticker="AAPL", direction="long",
                    mode=Mode.PAPER, status=SignalStatus.ACCEPTED)
    return OrderManagementSystem(
        signal_loader=lambda _: signal, broker_adapter=broker, persistence=_Persistence(),
        kill_switch_checker=checker, risk_checker=lambda **_: RiskDecision(True),
        order_planner=lambda *args, **kwargs: {"kind": "shares", "qty": 1.0,
                                               "notional": 100.0},
    )


def test_oms_gate_stops_before_broker_and_allowed_continues():
    blocked_broker = _Broker()
    result = _oms(lambda _: False, blocked_broker).submit_intent(
        _intent(), price=100.0, trade_size=100.0)
    assert result.code == "kill_switch_active"
    assert blocked_broker.submissions == []

    allowed_broker = _Broker()
    result = _oms(lambda _: True, allowed_broker).submit_intent(
        _intent(), price=100.0, trade_size=100.0)
    assert result.ok is True
    assert allowed_broker.submissions == ["AAPL"]


def test_telegram_global_switch_is_admin_only(fresh_db, monkeypatch):
    from stockbot.tgbot import bot

    service = KillSwitchService(persistence=fresh_db)
    monkeypatch.setattr(bot, "kill_switch_service", service)
    monkeypatch.setattr(bot, "ADMIN_CHAT_ID", 1)

    class Message:
        def __init__(self): self.texts = []
        async def reply_text(self, text, **kwargs): self.texts.append(text)

    denied = SimpleNamespace(effective_chat=SimpleNamespace(id=2), message=Message())
    asyncio.run(bot.cmd_killswitch(denied, SimpleNamespace(args=["on", "Test"])))
    assert service.global_status is None

    allowed = SimpleNamespace(effective_chat=SimpleNamespace(id=1), message=Message())
    asyncio.run(bot.cmd_killswitch(allowed, SimpleNamespace(args=["on", "Test"])))
    assert service.global_status.active is True


def test_web_admin_global_non_admin_personal(fresh_db, monkeypatch):
    from stockbot.web import webapp
    from stockbot.web import auth

    fresh_db.get_or_create_user(1, "admin")
    fresh_db.get_or_create_user(2, "user")
    service = KillSwitchService(persistence=fresh_db)
    monkeypatch.setattr(webapp, "kill_switch_service", service)
    monkeypatch.setattr(webapp.config, "ADMIN_CHAT_ID", 1)
    request = SimpleNamespace()

    monkeypatch.setattr(auth, "current_user", lambda request: fresh_db.get_user(2))
    response = webapp.app_settings_killswitch(request, enabled="1", reason="Pause")
    assert response.status_code == 303
    assert service.global_status is None
    assert service.user_status(2).active is True

    monkeypatch.setattr(auth, "current_user", lambda request: fresh_db.get_user(1))
    webapp.app_settings_killswitch(request, enabled="1", reason="Global")
    assert service.global_status.active is True
