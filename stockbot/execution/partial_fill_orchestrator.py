"""Ausführung der Partial-Fill-Policy mit injizierbaren IO-Grenzen."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Callable

from stockbot.core.domain import Order, Position
from stockbot.execution.partial_fill_policy import PartialFillDecision, decide_partial_fill_action

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PartialFillExecution:
    decision: PartialFillDecision
    executed: tuple[str, ...] = ()


def orchestrate_partial_fill(
    *, order: Order, position: Position, remaining_qty: float | None,
    order_submitted_at: datetime, now: datetime, active_orders: tuple[Order, ...],
    signal_stop_loss: float | None,
    submit_stop_sell: Callable[..., dict], cancel_order: Callable[[str], dict],
    persist_protective: Callable[..., Any], notifier: Callable[[str], Any],
) -> PartialFillExecution:
    """Entscheidet und führt genau die freigegebene Partial-Fill-Aktion aus."""
    decision = decide_partial_fill_action(
        order=order, position=position, active_orders=active_orders,
        remaining_qty=remaining_qty, order_submitted_at=order_submitted_at, now=now,
    )
    executed: list[str] = []
    if decision.action == "no_action_needed":
        return PartialFillExecution(decision)
    if decision.action == "resize_needed":
        notifier(f"Partial-Fill-Schutzorder für {order.ticker} muss angepasst werden: {decision.reason}")
        return PartialFillExecution(decision, ("notified",))
    if decision.action == "cancel_restorder":
        if not order.broker_order_id:
            log.warning("Partial-Fill-Restorder %s hat keine Broker-ID", order.id)
            return PartialFillExecution(decision)
        try:
            result = cancel_order(order.broker_order_id)
            if result.get("ok"):
                executed.append("cancelled_restorder")
            else:
                log.warning("Partial-Fill-Restorder %s nicht storniert: %s", order.id,
                            result.get("detail", "Brokerfehler"))
        except Exception as exc:
            log.warning("Partial-Fill-Restorder %s nicht storniert: %s", order.id,
                        type(exc).__name__)
        return PartialFillExecution(decision, tuple(executed))

    if signal_stop_loss is None:
        notifier(f"Keine Schutzorder für Partial Fill {order.ticker}: Signal-Stop-Loss fehlt.")
        return PartialFillExecution(decision, ("notified",))
    try:
        result = submit_stop_sell(
            order.ticker, qty=decision.target_qty, stop_price=float(signal_stop_loss),
        )
        if not result.get("ok") or not result.get("id"):
            log.warning("Partial-Fill-Schutzorder für %s fehlgeschlagen: %s", order.ticker,
                        result.get("detail", "Brokerfehler"))
            return PartialFillExecution(decision)
        executed.append("submitted_protective")
        try:
            persist_protective(
                source_order_id=order.id, trade_intent_id=order.trade_intent_id,
                user_id=order.user_id, ticker=order.ticker, qty=decision.target_qty,
                stop_price=float(signal_stop_loss), broker_order_id=str(result["id"]),
            )
            executed.append("persisted_protective")
        except Exception:
            notifier(
                f"Schutzorder {result['id']} für {order.ticker} wurde platziert, "
                "konnte aber nicht für die Dedup gespeichert werden."
            )
            log.exception("Partial-Fill-Schutzorder %s nicht persistiert", result["id"])
    except Exception as exc:
        log.warning("Partial-Fill-Schutzorder für %s nicht abgeschlossen: %s", order.ticker,
                    type(exc).__name__)
    return PartialFillExecution(decision, tuple(executed))
