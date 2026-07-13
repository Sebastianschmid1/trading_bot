"""Orchestration for idempotent asynchronous broker order events."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from typing import Any, Callable, Mapping

from stockbot.core.audit_log import AuditLog
from stockbot.core.domain import Mode, Order, Position, PositionEvent, PositionStatus
from stockbot.core.state_machine import assert_position_transition, position_transition_allowed
from stockbot.execution.oms import OrderManagementSystem, _as_order

log = logging.getLogger(__name__)

SUPPORTED_EVENTS = frozenset({
    "accepted", "rejected", "partial_fill", "fill", "cancelled", "expired", "replaced",
})


@dataclass(frozen=True)
class BrokerEventResult:
    order: Order
    position: Position | None = None
    position_event: PositionEvent | None = None
    remaining_qty: float | None = None
    deduplicated: bool = False


def _number(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return float(value)
    return None


def derive_position_from_fill(
    order: Order,
    *,
    event_type: str,
    payload: Mapping[str, Any],
    existing_position: Position | None = None,
    strategy_version_id: int | None = None,
    mode: Mode = Mode.PAPER,
) -> tuple[Position, PositionEvent | None, float | None]:
    """Derive an unpersisted position projection from cumulative broker fill data."""
    if event_type not in {"partial_fill", "fill"}:
        raise ValueError(f"Not a fill event: {event_type}")
    if existing_position and (
        existing_position.user_id != order.user_id or existing_position.ticker != order.ticker
    ):
        raise ValueError("Existing position does not belong to the order")

    filled_qty = _number(payload, "filled_qty", "cumulative_filled_qty")
    if filled_qty is None:
        event_qty = _number(payload, "qty", "fill_qty")
        if event_qty is not None:
            filled_qty = (existing_position.qty if existing_position else 0.0) + event_qty
        elif event_type == "fill" and order.qty is not None:
            filled_qty = float(order.qty)
        else:
            raise ValueError("Fill payload requires filled_qty, cumulative_filled_qty, qty, or fill_qty")
    if filled_qty < 0:
        raise ValueError("Filled quantity cannot be negative")

    remaining_qty = None
    if order.qty is not None:
        remaining_qty = max(float(order.qty) - filled_qty, 0.0)
    target_status = (
        PositionStatus.OPEN
        if (event_type == "fill" or remaining_qty == 0.0
            or (existing_position and existing_position.status == PositionStatus.OPEN))
        else PositionStatus.PENDING_OPEN
    )
    price = _number(payload, "filled_avg_price", "avg_fill_price", "price")
    opened_at = (
        payload.get("filled_at") or payload.get("occurred_at") or payload.get("timestamp")
    )

    if existing_position is None:
        version = strategy_version_id
        if version is None and payload.get("strategy_version_id") is not None:
            version = int(payload["strategy_version_id"])
        if version is None:
            raise ValueError("strategy_version_id is required for a new position")
        position = Position(
            id=None, user_id=order.user_id, ticker=order.ticker,
            strategy_version_id=version, mode=mode, status=PositionStatus.PENDING_OPEN,
            qty=filled_qty, avg_entry_price=price, opened_at=str(opened_at) if opened_at else None,
        )
    else:
        position = replace(
            existing_position, qty=filled_qty,
            avg_entry_price=price if price is not None else existing_position.avg_entry_price,
            opened_at=(str(opened_at) if opened_at else existing_position.opened_at),
        )

    previous_status = position.status
    if previous_status != target_status:
        if not position_transition_allowed(previous_status, target_status):
            assert_position_transition(previous_status, target_status)
        position = replace(position, status=target_status)

    position_event = None
    if position.id is not None:
        position_event = PositionEvent(
            id=None, position_id=position.id, event_type=event_type,
            from_status=previous_status.value, to_status=position.status.value,
            occurred_at=str(opened_at or ""),
        )
    return position, position_event, remaining_qty


def process_broker_event(
    oms: OrderManagementSystem,
    *,
    order_id: int,
    event_type: str,
    broker_event_id: str,
    payload: Mapping[str, Any] | None = None,
    existing_position: Position | None = None,
    strategy_version_id: int | None = None,
    mode: Mode = Mode.PAPER,
    audit_log: AuditLog | None = None,
    notifier: Callable[[BrokerEventResult], None] | None = None,
) -> BrokerEventResult:
    """Deduplicate, apply, project, audit, and notify one broker event."""
    normalized = event_type.lower()
    if normalized not in SUPPORTED_EVENTS:
        raise ValueError(f"Unsupported broker event: {event_type}")
    if not broker_event_id:
        raise ValueError("broker_event_id is required")

    row = oms.persistence.get_oms_order(order_id)
    if row is None:
        raise KeyError(f"Unknown OMS order {order_id}")
    old_order = _as_order(row)
    if any(
        event.get("broker_event_id") == broker_event_id
        for event in oms.persistence.get_oms_order_events(order_id)
    ):
        return BrokerEventResult(
            order=old_order, position=existing_position, deduplicated=True,
        )

    event_payload = dict(payload or {})
    position = None
    position_event = None
    remaining_qty = None
    if normalized in {"partial_fill", "fill"}:
        position, position_event, remaining_qty = derive_position_from_fill(
            old_order, event_type=normalized, payload=event_payload,
            existing_position=existing_position, strategy_version_id=strategy_version_id,
            mode=mode,
        )
    order = oms.process_broker_event(
        order_id, normalized, event_payload, broker_event_id=broker_event_id,
    )

    result = BrokerEventResult(
        order=order, position=position, position_event=position_event,
        remaining_qty=remaining_qty,
    )
    if audit_log is not None:
        audit_log.record(
            actor="broker", entity_type="order", entity_id=str(order.id), action=normalized,
            old_state=old_order.status.value, new_state=order.status.value,
            trace_id=broker_event_id, source_channel="broker_event_worker",
            user_id=order.user_id, metadata=event_payload,
        )
    notify = notifier if notifier is not None else oms.notifier
    if notify is not None:
        try:
            notify(result)
        except Exception as exc:
            log.warning("Broker event notification hook failed: %s", type(exc).__name__)
    return result
