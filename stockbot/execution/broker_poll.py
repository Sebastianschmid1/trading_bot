"""Polling-Bridge von Broker-Orderstatus zu idempotenten OMS-Events."""

from __future__ import annotations

from hashlib import sha256
import logging
from typing import Any, Callable, Iterable, Mapping

from stockbot.core.domain import Mode, OrderStatus, Position
from stockbot.core import metrics
from stockbot.execution.broker_event_worker import BrokerEventResult, process_broker_event

log = logging.getLogger(__name__)

_STATUS_EVENTS = {
    "accepted": "accepted",
    "new": "accepted",
    "pending_new": "accepted",
    "filled": "fill",
    "partially_filled": "partial_fill",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "expired": "expired",
    "rejected": "rejected",
}

_UNCHANGED_STATUSES = {
    "accepted": OrderStatus.ACCEPTED_BY_BROKER.value,
    "new": OrderStatus.ACCEPTED_BY_BROKER.value,
    "pending_new": OrderStatus.ACCEPTED_BY_BROKER.value,
    "filled": OrderStatus.FILLED.value,
    "canceled": OrderStatus.CANCELLED.value,
    "cancelled": OrderStatus.CANCELLED.value,
    "expired": OrderStatus.EXPIRED.value,
    "rejected": OrderStatus.REJECTED.value,
}


def _filled_qty(response: Mapping[str, Any]) -> str:
    value = response.get("filled_qty", response.get("cumulative_filled_qty", ""))
    if value in (None, ""):
        return ""
    try:
        return format(float(value), ".15g")
    except (TypeError, ValueError):
        return str(value)


def broker_event_id(broker_order_id: str, status: str,
                    response: Mapping[str, Any]) -> str:
    """Liefert eine brokerseitige Update-ID oder einen stabilen Poll-Zustands-Hash."""
    for key in ("broker_event_id", "event_id", "update_id"):
        value = response.get(key)
        if value not in (None, ""):
            return str(value)
    state = f"{broker_order_id}\0{status}\0{_filled_qty(response)}"
    return f"poll:{sha256(state.encode('utf-8')).hexdigest()}"


def map_broker_status(order: Mapping[str, Any], response: Mapping[str, Any]
                      ) -> list[tuple[str, str, dict[str, Any]]]:
    """Bildet einen Broker-Snapshot auf höchstens ein OMS-Event ab."""
    status = str(response.get("status") or "").strip().lower()
    event_type = _STATUS_EVENTS.get(status)
    if event_type is None:
        return []
    if _UNCHANGED_STATUSES.get(status) == str(order.get("status") or ""):
        return []
    # Bei partial_fill kann derselbe Status einen neuen kumulativen Fill-Stand tragen.
    broker_order_id = str(order.get("broker_order_id") or "")
    if not broker_order_id:
        return []
    payload = dict(response)
    return [(event_type, broker_event_id(broker_order_id, status, response), payload)]


def poll_broker_orders(
    oms,
    orders: Iterable[Mapping[str, Any]],
    *,
    status_fetcher: Callable[[str], Mapping[str, Any]],
    strategy_version_id: int | None = None,
    mode: Mode = Mode.PAPER,
    partial_fill_handler: Callable[[BrokerEventResult], Any] | None = None,
) -> list[BrokerEventResult]:
    """Fragt Orders einzeln ab; Brokerfehler bleiben auf die jeweilige Order begrenzt."""
    results: list[BrokerEventResult] = []
    positions: dict[int, Position] = {}
    successful = True
    for order in orders:
        order_id = int(order["id"])
        broker_order_id = str(order.get("broker_order_id") or "")
        if not broker_order_id:
            continue
        try:
            response = status_fetcher(broker_order_id)
            if not response.get("ok", True):
                raise RuntimeError(str(response.get("detail") or "Brokerstatus nicht abrufbar"))
            events = map_broker_status(order, response)
            for event_type, event_id, payload in events:
                result = process_broker_event(
                    oms, order_id=order_id, event_type=event_type,
                    broker_event_id=event_id, payload=payload,
                    existing_position=positions.get(order_id),
                    strategy_version_id=(order.get("strategy_version_id")
                                         if order.get("strategy_version_id") is not None
                                         else strategy_version_id),
                    mode=mode,
                )
                if result.position is not None:
                    positions[order_id] = result.position
                results.append(result)
                if (event_type == "partial_fill" and not result.deduplicated
                        and partial_fill_handler is not None):
                    try:
                        partial_fill_handler(result)
                    except Exception as exc:
                        log.warning(
                            "Partial-Fill-Orchestrierung für OMS-Order %s fehlgeschlagen: %s",
                            order_id, type(exc).__name__,
                        )
        except Exception as exc:
            successful = False
            log.warning(
                "Broker-Poll für OMS-Order %s fehlgeschlagen: %s",
                order_id, type(exc).__name__,
            )
    if successful:
        metrics.broker_poll_succeeded()
    return results
