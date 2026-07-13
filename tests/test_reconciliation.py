"""Offline-Tests für den OMS-Reconciliation-Seam (OMS-007)."""

from stockbot.core.domain import Mode, Order, OrderStatus, Position, PositionStatus
from stockbot.execution.broker_adapter import BrokerAccount, BrokerOrder, BrokerPosition
from stockbot.execution.reconciliation import (
    reconcile_account,
    reconcile_orders,
    reconcile_positions,
    run_daily_full_reconciliation,
    run_periodic_reconciliation,
)


class FakeAdapter:
    """Nur die drei vom Reconciler konsumierten BrokerAdapter-Methoden."""

    def __init__(self, *, orders=None, positions=None, account=None):
        self.orders = orders or []
        self.positions = positions or []
        self.account = account or BrokerAccount(ok=True, cash=1_000, buying_power=2_000)
        self.calls = []

    def list_open_orders(self):
        self.calls.append("orders")
        return self.orders

    def list_positions(self):
        self.calls.append("positions")
        return self.positions

    def get_account(self):
        self.calls.append("account")
        return self.account


def position(ticker="AAPL", qty=2.0):
    return Position(
        id=1,
        user_id=7,
        ticker=ticker,
        strategy_version_id=3,
        mode=Mode.PAPER,
        status=PositionStatus.OPEN,
        qty=qty,
    )


def order(
    *,
    ticker="AAPL",
    qty=2.0,
    status=OrderStatus.ACCEPTED_BY_BROKER,
    broker_order_id="broker-1",
    client_order_id="client-1",
):
    return Order(
        id=11,
        trade_intent_id=5,
        user_id=7,
        ticker=ticker,
        side="buy",
        qty=qty,
        status=status,
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
    )


def broker_position(symbol="AAPL", qty=2.0):
    return BrokerPosition(symbol=symbol, qty=qty, avg_entry_price=100)


def broker_order(*, order_id="broker-1", client_order_id="client-1", qty=2.0):
    return BrokerOrder(
        order_id=order_id,
        client_order_id=client_order_id,
        symbol="AAPL",
        status="accepted",
        qty=qty,
    )


def test_identical_snapshot_has_no_findings_and_is_ok():
    adapter = FakeAdapter(orders=[broker_order()], positions=[broker_position()])

    report = run_periodic_reconciliation(
        adapter,
        internal_positions=[position()],
        internal_orders=[order()],
        expected_cash=1_000,
        expected_buying_power=2_000,
    )

    assert report.findings == []
    assert report.ok is True
    assert report.started_at <= report.finished_at
    assert adapter.calls == ["orders", "positions", "account"]


def test_unknown_broker_position_is_reported():
    findings = reconcile_positions([], [broker_position("MSFT", 3)])

    assert [finding.kind for finding in findings] == ["unknown_broker_position"]
    assert findings[0].symbol == "MSFT"
    assert findings[0].details["broker_qty"] == 3


def test_missing_broker_position_is_reported():
    findings = reconcile_positions([position("MSFT", 3)], [])

    assert [finding.kind for finding in findings] == ["missing_broker_position"]
    assert findings[0].symbol == "MSFT"
    assert findings[0].details["internal_qty"] == 3


def test_position_quantity_mismatch_is_reported():
    findings = reconcile_positions([position(qty=2)], [broker_position(qty=2.5)])

    assert [finding.kind for finding in findings] == ["qty_mismatch"]
    assert findings[0].details == {
        "entity": "position", "internal_qty": 2.0, "broker_qty": 2.5}


def test_position_quantity_uses_float_tolerance():
    findings = reconcile_positions(
        [position(qty=0.1 + 0.2)], [broker_position(qty=0.3)])

    assert findings == []


def test_open_internal_order_without_broker_counterpart_is_reported():
    findings = reconcile_orders([order()], [])

    assert [finding.kind for finding in findings] == ["missing_broker_order"]
    assert findings[0].internal_order_id == 11


def test_broker_order_without_internal_counterpart_is_reported():
    findings = reconcile_orders([], [broker_order()])

    assert [finding.kind for finding in findings] == ["unknown_broker_order"]
    assert findings[0].broker_order_id == "broker-1"


def test_order_can_match_by_client_order_id():
    findings = reconcile_orders(
        [order(broker_order_id=None)],
        [broker_order(order_id="broker-assigned")],
    )

    assert findings == []


def test_order_state_and_quantity_mismatches_are_reported():
    internal = order(status=OrderStatus.PARTIALLY_FILLED, qty=3)

    findings = reconcile_orders([internal], [broker_order(qty=2)])

    assert [finding.kind for finding in findings] == [
        "order_state_mismatch", "qty_mismatch"]


def test_cash_mismatch_outside_tolerance_is_reported():
    account = BrokerAccount(ok=True, cash=102, buying_power=200)

    findings = reconcile_account(100, None, account, tolerance_pct=1)

    assert [finding.kind for finding in findings] == ["cash_mismatch"]


def test_cash_difference_inside_tolerance_is_accepted():
    account = BrokerAccount(ok=True, cash=101, buying_power=200)

    findings = reconcile_account(100, None, account, tolerance_pct=1)

    assert findings == []


def test_account_checks_are_optional():
    account = BrokerAccount(ok=True, cash=999, buying_power=999)

    assert reconcile_account(None, None, account, tolerance_pct=0) == []


def test_daily_report_contains_findings():
    report = run_daily_full_reconciliation(
        FakeAdapter(positions=[broker_position("TSLA", 4)]),
        internal_positions=[],
        internal_orders=[],
    )

    assert report.ok is False
    assert "unknown_broker_position" in report.text_report
    assert "TSLA" in report.text_report
