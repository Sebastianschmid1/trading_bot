"""Offline-Tests für den BrokerAdapter-Seam (OMS-004)."""

from types import SimpleNamespace

import pytest

from stockbot.execution.broker_adapter import (
    AlpacaBrokerAdapter,
    BrokerAccount,
    BrokerActionResult,
    BrokerAdapter,
    BrokerOrder,
    BrokerPosition,
)


class FakeClient:
    """Minimaler Alpaca-TradingClient-Stand-in; kein Netzwerk und keine Keys."""

    _paper = True

    def __init__(self):
        self.submitted = []
        self.canceled = []
        self.closed = []
        self.order_filter = None

    def submit_order(self, request):
        self.submitted.append(request)
        return SimpleNamespace(id="submitted-1")

    def cancel_order_by_id(self, order_id):
        self.canceled.append(order_id)

    def close_position(self, symbol):
        self.closed.append(symbol)
        return SimpleNamespace(id="close-1")

    def get_order_by_id(self, order_id):
        return SimpleNamespace(
            id=order_id, status="filled", filled_qty="2", filled_avg_price="101.25")

    def get_orders(self, *, filter):
        self.order_filter = filter
        return [SimpleNamespace(
            id="open-1", client_order_id="intent-1", symbol="AAPL", side="buy",
            status="accepted", qty="3", notional=None, limit_price="100.50",
            filled_qty="1", filled_avg_price="100.25",
        )]

    def get_all_positions(self):
        return [SimpleNamespace(
            symbol="AAPL", qty="3", avg_entry_price="99.5", side="long",
            asset_class="us_equity", unrealized_pl="4.5",
        )]

    def get_account(self):
        return SimpleNamespace(
            status="ACTIVE", cash="10000", buying_power="20000", currency="USD")


def test_broker_adapter_is_abstract():
    with pytest.raises(TypeError):
        BrokerAdapter()


def test_submit_order_delegates_buy_to_existing_broker_function():
    client = FakeClient()
    result = AlpacaBrokerAdapter(client).submit_order(
        "AAPL", side="BUY", notional=50, client_order_id="intent-1")

    assert result == BrokerOrder(
        order_id="submitted-1", symbol="AAPL", side="buy", notional=50,
        client_order_id="intent-1", detail="AAPL $50.00 (Bruchteile)")
    assert len(client.submitted) == 1
    assert float(client.submitted[0].notional) == 50.0
    assert client.submitted[0].client_order_id == "intent-1"


def test_cancel_order_delegates_to_existing_broker_function():
    client = FakeClient()
    result = AlpacaBrokerAdapter(client).cancel_order("order-1")

    assert result == BrokerActionResult(
        ok=True, performed=True, order_id="order-1", detail="order order-1 canceled")
    assert client.canceled == ["order-1"]


def test_replace_order_explicitly_rejects_unsafe_emulation():
    with pytest.raises(NotImplementedError, match=r"Cancel\+Submit"):
        AlpacaBrokerAdapter(FakeClient()).replace_order("order-1", qty=2)


def test_close_position_delegates_to_existing_broker_function():
    client = FakeClient()
    result = AlpacaBrokerAdapter(client).close_position("AAPL")

    assert result == BrokerActionResult(
        ok=True, performed=True, order_id="close-1", detail="AAPL geschlossen")
    assert client.closed == ["AAPL"]


def test_get_order_converts_existing_status_response():
    result = AlpacaBrokerAdapter(FakeClient()).get_order("order-1")

    assert result == BrokerOrder(
        order_id="order-1", status="filled", filled_qty=2,
        filled_avg_price=101.25)


def test_list_open_orders_uses_open_filter_and_returns_value_objects():
    client = FakeClient()
    result = AlpacaBrokerAdapter(client).list_open_orders()

    assert result == [BrokerOrder(
        order_id="open-1", status="accepted", symbol="AAPL", side="buy", qty=3,
        limit_price=100.5, filled_qty=1, filled_avg_price=100.25,
        client_order_id="intent-1")]
    assert str(client.order_filter.status).lower().endswith("open")


def test_list_positions_converts_existing_broker_response():
    result = AlpacaBrokerAdapter(FakeClient()).list_positions()

    assert result == [BrokerPosition(
        symbol="AAPL", qty=3, avg_entry_price=99.5, side="long",
        asset_class="us_equity", unrealized_pl=4.5)]


def test_stream_order_events_explicitly_rejects_missing_stream_lifecycle():
    with pytest.raises(NotImplementedError, match="TradingStream"):
        AlpacaBrokerAdapter(FakeClient()).stream_order_events()


def test_get_account_converts_existing_summary_response():
    result = AlpacaBrokerAdapter(FakeClient()).get_account()

    assert result == BrokerAccount(
        ok=True, paper=True, status="ACTIVE", cash=10000, buying_power=20000,
        currency="USD", detail="OK")
