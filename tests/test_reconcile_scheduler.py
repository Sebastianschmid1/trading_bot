from stockbot.core.domain import OrderStatus, PositionStatus
from stockbot.execution.broker_adapter import BrokerAccount, BrokerPosition
from stockbot.execution.reconcile_scheduler import (
    build_internal_state,
    format_admin_alarm,
    reconcile_user_oms,
)
from stockbot.execution.reconciliation import ReconciliationFinding, ReconciliationReport


class FakePersistence:
    def get_active_trades(self, user_id):
        return [{
            "id": 11,
            "user_id": user_id,
            "ticker": "AAPL",
            "direction": "long",
            "broker_filled_qty": 2.0,
            "entry": 190.0,
            "created_at": "2026-07-16 14:00:00",
            "signal_json": "{}",
        }]

    def get_open_oms_orders(self):
        return [{
            "id": 21,
            "trade_intent_id": 31,
            "user_id": 7,
            "ticker": "MSFT",
            "side": "buy",
            "qty": 3.0,
            "notional": None,
            "limit_price": None,
            "status": "accepted_by_broker",
            "broker_order_id": "broker-21",
            "client_order_id": "client-21",
            "idempotency_key": "key-21",
            "created_at": "2026-07-16 14:00:00",
            "updated_at": "2026-07-16 14:01:00",
        }]


class FakeAdapter:
    def __init__(self, client):
        self.client = client

    def list_open_orders(self):
        return []

    def list_positions(self):
        return [BrokerPosition(symbol="AAPL", qty=1.0, avg_entry_price=190.0)]

    def get_account(self):
        return BrokerAccount(ok=True, cash=1000.0, buying_power=2000.0)


def test_build_internal_state_maps_open_trade_and_order():
    positions, orders = build_internal_state(7, persistence=FakePersistence())

    assert len(positions) == 1
    assert positions[0].ticker == "AAPL"
    assert positions[0].qty == 2.0
    assert positions[0].status == PositionStatus.OPEN
    assert len(orders) == 1
    assert orders[0].ticker == "MSFT"
    assert orders[0].qty == 3.0
    assert orders[0].status == OrderStatus.ACCEPTED_BY_BROKER


def test_reconcile_user_oms_reports_broker_difference(monkeypatch):
    monkeypatch.setattr(
        "stockbot.execution.reconcile_scheduler.AlpacaBrokerAdapter", FakeAdapter,
    )

    report = reconcile_user_oms(
        {"user_id": 7}, object(), persistence=FakePersistence(),
    )

    assert {(finding.kind, finding.symbol) for finding in report.findings} == {
        ("qty_mismatch", "AAPL"),
        ("missing_broker_order", "MSFT"),
    }


def test_reconcile_user_oms_returns_empty_report_on_adapter_error(monkeypatch):
    class BrokenAdapter(FakeAdapter):
        def list_open_orders(self):
            raise RuntimeError("offline")

    monkeypatch.setattr(
        "stockbot.execution.reconcile_scheduler.AlpacaBrokerAdapter", BrokenAdapter,
    )

    report = reconcile_user_oms(
        {"user_id": 7}, object(), persistence=FakePersistence(),
    )

    assert report.findings == []


def test_format_admin_alarm_bundles_findings():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    reports = {
        7: ReconciliationReport(
            findings=[ReconciliationFinding(kind="qty_mismatch", symbol="AAPL")],
            started_at=now,
            finished_at=now,
        ),
        9: ReconciliationReport(
            findings=[ReconciliationFinding(kind="unknown_broker_order", symbol="MSFT")],
            started_at=now,
            finished_at=now,
        ),
    }

    message = format_admin_alarm(reports)

    assert message is not None
    assert "AAPL" in message and "qty_mismatch" in message
    assert "MSFT" in message and "unknown_broker_order" in message


def test_format_admin_alarm_returns_none_without_findings():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    report = ReconciliationReport(findings=[], started_at=now, finished_at=now)

    assert format_admin_alarm({7: report}) is None
