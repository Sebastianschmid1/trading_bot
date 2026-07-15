from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stockbot.core.domain import Mode, Signal, SignalStatus, TradeIntent
from stockbot.execution import risk_context
from stockbot.execution.oms import OrderManagementSystem


def _signal() -> Signal:
    return Signal(
        id=17, strategy_version_id=1, ticker="AAPL", direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )


def _intent(key: str = "risk:42:17") -> TradeIntent:
    return TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="test",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key=key,
    )


def test_signal_context_loads_positions_stop_and_market(monkeypatch):
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [
        {"ticker": "MSFT"}, {"ticker": "aapl"},
    ])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {
        "signal": {"stop_loss": 95.0},
    })

    context = risk_context.signal_context(_intent(), _signal())

    assert context["open_position_count"] == 2
    assert context["has_existing_ticker_position"] is True
    assert context["risk_profile"].user_id == 42
    assert context["stop_price"] == 95.0
    # market_open wird bewusst nicht gesetzt (Extended-Hours; siehe risk_context.py)
    assert "market_open" not in context


def test_signal_context_omits_missing_stop(monkeypatch):
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})

    assert "stop_price" not in risk_context.signal_context(_intent(), _signal())


def test_account_context_loads_available_account_fields():
    account = SimpleNamespace(equity="10000", buying_power="7500", status="ACTIVE",
                              cash="5000", currency="USD")
    context = risk_context.account_context(
        SimpleNamespace(get_account=lambda: account), user_id=42,
    )

    assert context == {
        "account_value": 10000.0, "buying_power": 7500.0, "broker_status": "ACTIVE",
    }
    assert "realized_pnl_today" not in context


def test_account_context_broker_failure_does_not_raise():
    def fail():
        raise RuntimeError("offline")

    assert risk_context.account_context(SimpleNamespace(get_account=fail), user_id=42) == {}


class _Persistence:
    def __init__(self):
        self.orders = {}

    def get_order_by_idempotency_key(self, key):
        return None

    def create_oms_order(self, intent, **kwargs):
        row = {
            "id": 1, "trade_intent_id": 1, "user_id": intent.user_id,
            "ticker": kwargs["ticker"], "side": "buy", "qty": kwargs["qty"],
            "notional": kwargs["notional"], "limit_price": kwargs["limit_price"],
            "status": "created", "broker_order_id": None, "client_order_id": "oms-1",
            "idempotency_key": intent.idempotency_key, "created_at": None, "updated_at": None,
        }
        self.orders[1] = row
        return row, True

    def transition_oms_order(self, order_id, *, to_status, **kwargs):
        self.orders[order_id].update(status=to_status)
        if kwargs.get("broker_order_id"):
            self.orders[order_id]["broker_order_id"] = kwargs["broker_order_id"]
        return self.orders[order_id]


class _Broker:
    def submit_buy(self, symbol, **kwargs):
        return {"ok": True, "id": "paper-1", "status": "filled"}


def test_real_oms_applies_loaded_position_limit_and_allows_clean_case(monkeypatch):
    active = [{"ticker": f"T{i}"} for i in range(5)]
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: active)
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    service = OrderManagementSystem(
        signal_loader=lambda signal_id: _signal(), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )
    callsite_context = {"entry_price": 100.0, "candidate_notional": 100.0}

    rejected = service.submit_intent(
        _intent(), price=100.0, trade_size=100.0, risk_context=callsite_context,
    )
    assert rejected.ok is False and rejected.code == "max_positions_reached"

    active.clear()
    accepted = service.submit_intent(
        _intent("risk:42:18"), price=100.0, trade_size=100.0,
        risk_context=callsite_context,
    )
    assert accepted.ok is True
