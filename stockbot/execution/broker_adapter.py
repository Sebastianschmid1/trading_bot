"""Broker-Ausführungsgrenze für das OMS (Phase 4 / OMS-004, Plan.md §12.4).

Das Modul definiert nur den austauschbaren Seam und eine Alpaca-Implementierung. Bestehende
Live-Pfade werden in OMS-004 bewusst noch nicht auf den Adapter umverdrahtet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from stockbot.broker import client as broker_client


@dataclass(frozen=True)
class BrokerOrder:
    """SDK-unabhängiger Broker-Order-Snapshot ohne interne OMS-IDs."""

    order_id: str
    ok: bool = True
    status: str = ""
    symbol: str = ""
    side: str = ""
    qty: float | None = None
    notional: float | None = None
    limit_price: float | None = None
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    client_order_id: str = ""
    detail: str = ""


@dataclass(frozen=True)
class BrokerActionResult:
    """Ergebnis einer Cancel-/Close-Aktion."""

    ok: bool
    performed: bool
    order_id: str = ""
    detail: str = ""


@dataclass(frozen=True)
class BrokerPosition:
    """Broker-Position ohne die erst im OMS bekannten Nutzer-/Strategiereferenzen."""

    symbol: str
    qty: float
    avg_entry_price: float
    side: str = ""
    asset_class: str = ""
    unrealized_pl: float = 0.0


@dataclass(frozen=True)
class BrokerAccount:
    """Kompakter Kontosnapshot für Order-Vorprüfungen."""

    ok: bool
    paper: bool = True
    status: str = ""
    cash: float = 0.0
    buying_power: float = 0.0
    currency: str = "USD"
    detail: str = ""


@dataclass(frozen=True)
class BrokerOrderEvent:
    """Broker-Ereignis vor Zuordnung zur internen ``domain.OrderEvent``-ID."""

    order_id: str
    event_type: str
    occurred_at: datetime
    broker_event_id: str = ""
    payload: dict = field(default_factory=dict)


class BrokerAdapter(ABC):
    """Brokerunabhängiges Order-/Positions-Interface aus Plan.md §12.4."""

    @abstractmethod
    def submit_order(
        self, symbol: str, *, side: str, qty: float | None = None,
        notional: float | None = None, limit_price: float | None = None,
        extended_hours: bool = False, client_order_id: str | None = None,
        asset_class: str = "equity",
    ) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> BrokerActionResult:
        raise NotImplementedError

    @abstractmethod
    def replace_order(
        self, order_id: str, *, qty: float | None = None,
        limit_price: float | None = None,
    ) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    def close_position(self, symbol: str) -> BrokerActionResult:
        raise NotImplementedError

    @abstractmethod
    def get_order(self, order_id: str) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    def list_open_orders(self) -> list[BrokerOrder]:
        raise NotImplementedError

    @abstractmethod
    def list_positions(self) -> list[BrokerPosition]:
        raise NotImplementedError

    @abstractmethod
    def stream_order_events(self) -> Iterator[BrokerOrderEvent]:
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> BrokerAccount:
        raise NotImplementedError


class AlpacaBrokerAdapter(BrokerAdapter):
    """Adapter auf die bestehenden Funktionen in :mod:`stockbot.broker.client`.

    ``client`` ist injizierbar, damit Aufrufer und Tests ohne globale Keys und Netzwerk arbeiten.
    """

    def __init__(self, client=None):
        self._client = client

    def submit_order(
        self, symbol: str, *, side: str, qty: float | None = None,
        notional: float | None = None, limit_price: float | None = None,
        extended_hours: bool = False, client_order_id: str | None = None,
        asset_class: str = "equity",
    ) -> BrokerOrder:
        side_upper = side.upper()
        if asset_class == "option" and side_upper == "BUY":
            if qty is None or not float(qty).is_integer():
                raise ValueError("Optionskäufe benötigen eine ganzzahlige qty.")
            result = broker_client.submit_option_buy(symbol, int(qty), client=self._client)
        elif side_upper == "BUY":
            result = broker_client.submit_buy(
                symbol, notional=notional, qty=qty, limit_price=limit_price,
                extended_hours=extended_hours, client_order_id=client_order_id,
                client=self._client,
            )
        elif side_upper == "SELL":
            if qty is None:
                raise ValueError("Verkaufsorders benötigen qty.")
            result = broker_client.submit_exit_order(
                symbol, side=side_upper, qty=qty, limit_price=limit_price,
                extended_hours=extended_hours, client=self._client,
            )
        else:
            raise ValueError("side muss BUY oder SELL sein.")
        return BrokerOrder(
            order_id=str(result.get("id", "")), ok=bool(result.get("ok")), symbol=symbol,
            side=side_upper.lower(), qty=qty, notional=notional, limit_price=limit_price,
            client_order_id=client_order_id or "", detail=str(result.get("detail", "")),
        )

    def cancel_order(self, order_id: str) -> BrokerActionResult:
        result = broker_client.cancel_order(order_id, client=self._client)
        return BrokerActionResult(
            ok=bool(result.get("ok")), performed=bool(result.get("canceled")),
            order_id=order_id, detail=str(result.get("detail", "")),
        )

    def replace_order(
        self, order_id: str, *, qty: float | None = None,
        limit_price: float | None = None,
    ) -> BrokerOrder:
        """Nicht implementiert: Die bestehende Broker-Schicht hat keine sichere Replace-Semantik.

        Insbesondere darf Replace nicht still als Cancel+Submit nachgebildet werden, weil ein Fill
        zwischen beiden Calls sonst Doppelorders oder eine falsche Restmenge erzeugen kann.
        """
        raise NotImplementedError(
            "Order-Replace ist in stockbot.broker.client noch nicht implementiert; eine nicht-atomare "
            "Cancel+Submit-Simulation wäre bei konkurrierenden Fills unsicher.")

    def close_position(self, symbol: str) -> BrokerActionResult:
        result = broker_client.close_position(symbol, client=self._client)
        return BrokerActionResult(
            ok=bool(result.get("ok")), performed=bool(result.get("closed")),
            order_id=str(result.get("id", "")), detail=str(result.get("detail", "")),
        )

    def get_order(self, order_id: str) -> BrokerOrder:
        result = broker_client.get_order_status(order_id, client=self._client)
        return BrokerOrder(
            order_id=order_id, ok=bool(result.get("ok")), status=str(result.get("status", "")),
            filled_qty=float(result.get("filled_qty", 0) or 0),
            filled_avg_price=(float(result["filled_avg_price"])
                              if result.get("filled_avg_price") is not None else None),
            detail=str(result.get("detail", "")),
        )

    def list_open_orders(self) -> list[BrokerOrder]:
        return [self._order_from_dict(order) for order in
                broker_client.list_open_orders(client=self._client)]

    def list_positions(self) -> list[BrokerPosition]:
        return [BrokerPosition(
            symbol=str(position["symbol"]), qty=float(position["qty"]),
            avg_entry_price=float(position["avg_entry"]), side=str(position.get("side", "")),
            asset_class=str(position.get("asset_class", "")),
            unrealized_pl=float(position.get("unrealized_pl", 0) or 0),
        ) for position in broker_client.list_positions(client=self._client)]

    def stream_order_events(self) -> Iterator[BrokerOrderEvent]:
        """Nicht implementiert: ``broker.client`` besitzt noch keinen TradingStream-Lifecycle.

        Ein Polling-Generator wäre kein Ereignisstream und könnte Partial Fills verlieren; die
        Methode lehnt daher bis zur echten Alpaca-Streaming-Anbindung ausdrücklich ab.
        """
        raise NotImplementedError(
            "Order-Event-Streaming ist noch nicht an Alpacas TradingStream angebunden.")

    def get_account(self) -> BrokerAccount:
        result = broker_client.account_summary(client=self._client)
        return BrokerAccount(
            ok=bool(result.get("ok")), paper=bool(result.get("paper", True)),
            status=str(result.get("status", "")), cash=float(result.get("cash", 0) or 0),
            buying_power=float(result.get("buying_power", 0) or 0),
            currency=str(result.get("currency", "USD")), detail=str(result.get("detail", "")),
        )

    @staticmethod
    def _order_from_dict(order: dict) -> BrokerOrder:
        return BrokerOrder(
            order_id=str(order.get("id", "")), status=str(order.get("status", "")),
            symbol=str(order.get("symbol", "")), side=str(order.get("side", "")),
            qty=float(order["qty"]) if order.get("qty") is not None else None,
            notional=float(order["notional"]) if order.get("notional") is not None else None,
            limit_price=(float(order["limit_price"])
                         if order.get("limit_price") is not None else None),
            filled_qty=float(order.get("filled_qty", 0) or 0),
            filled_avg_price=(float(order["filled_avg_price"])
                              if order.get("filled_avg_price") is not None else None),
            client_order_id=str(order.get("client_order_id", "")),
        )
