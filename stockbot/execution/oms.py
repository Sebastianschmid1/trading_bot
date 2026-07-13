"""Idempotent order-management pipeline for long-only entries.

Web and Telegram hand a :class:`TradeIntent` to this service.  Broker access,
notification delivery and signal/context loading are injected so this module has
no dependency on either UI channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Mapping

from stockbot import config
from stockbot.broker import client as default_broker
from stockbot.broker import sizing
from stockbot.core import db, risk
from stockbot.core.domain import Mode, Order, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.core.state_machine import assert_order_transition

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OMSResult:
    ok: bool
    order: Order | None = None
    reason: str = ""
    code: str = ""
    idempotent: bool = False


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _as_order(row: Mapping[str, Any]) -> Order:
    return Order(
        id=int(row["id"]), trade_intent_id=int(row["trade_intent_id"]),
        user_id=int(row["user_id"]), ticker=str(row["ticker"]), side=str(row["side"]),
        qty=row.get("qty"), notional=row.get("notional"), limit_price=row.get("limit_price"),
        status=OrderStatus(row["status"]), broker_order_id=row.get("broker_order_id"),
        client_order_id=row.get("client_order_id"), idempotency_key=row.get("idempotency_key"),
        created_at=row.get("created_at"), updated_at=row.get("updated_at"),
    )


class OrderManagementSystem:
    """The single creation boundary for new long-only orders."""

    def __init__(
        self, *, signal_loader: Callable[[int], Signal | None],
        context_loader: Callable[[TradeIntent, Signal], Mapping[str, Any]] | None = None,
        broker_client: Any = None, broker_adapter: Any = None,
        risk_checker: Callable[..., risk.RiskDecision] = risk.pretrade_check,
        order_planner: Callable[..., dict] = sizing.plan_order,
        notifier: Callable[[OMSResult], None] | None = None,
        persistence: Any = db,
    ):
        self.signal_loader = signal_loader
        self.context_loader = context_loader
        # A fake adapter commonly calls itself a client.  Raw Alpaca clients do not
        # expose submit_buy and are passed through to the production adapter.
        if broker_adapter is None and broker_client is not None and hasattr(broker_client, "submit_buy"):
            self.broker = broker_client
            self.transport_client = None
        else:
            self.broker = broker_adapter or default_broker
            self.transport_client = broker_client
        self.risk_checker = risk_checker
        self.order_planner = order_planner
        self.notifier = notifier
        self.persistence = persistence

    def submit_intent(
        self, intent: TradeIntent, *, price: float | None = None,
        trade_size: float | None = None, leverage: float = 1.0,
        risk_context: Mapping[str, Any] | None = None,
        broker_client: Any = None,
    ) -> OMSResult:
        """Validate, risk-check, persist and submit one user action."""
        invalid = self._validate_intent(intent)
        if invalid:
            return self._finish(invalid)

        existing = self.persistence.get_order_by_idempotency_key(intent.idempotency_key)
        if existing:
            return self._finish(self._existing_result(existing))

        signal = self.signal_loader(intent.signal_id)
        invalid = self._validate_signal(intent, signal)
        if invalid:
            return self._finish(invalid)
        assert signal is not None

        context: dict[str, Any] = {}
        if self.context_loader:
            context.update(self.context_loader(intent, signal) or {})
        context.update(risk_context or {})
        if price is not None:
            context["price"] = price
        if trade_size is not None:
            context["trade_size"] = trade_size
        context.setdefault("leverage", leverage)

        risk_args = {k: v for k, v in context.items()
                     if k not in {"price", "trade_size", "extended", "roundup_factor"}}
        risk_args["signal_status"] = signal.status
        risk_args["signal_expires_at"] = _parse_timestamp(signal.expires_at)
        decision = self.risk_checker(**risk_args)
        if not decision.ok:
            return self._finish(OMSResult(False, reason=decision.reason, code=decision.code))

        entry_price = context.get("price")
        budget = context.get("trade_size")
        if entry_price is None or budget is None:
            return self._finish(OMSResult(False, reason="Kurs und Trade-Budget fehlen.",
                                          code="order_context_missing"))
        plan = self.order_planner(
            float(entry_price), float(budget), float(context.get("leverage", 1.0)),
            option_selector=None, extended=bool(context.get("extended", False)),
            roundup_factor=float(context.get("roundup_factor", config.SHARE_ROUNDUP_FACTOR)),
        )
        if plan.get("kind") != "shares":
            return self._finish(OMSResult(False, reason=plan.get("reason", "Kein gueltiger Aktien-Orderplan."),
                                          code="order_plan_rejected"))

        row, created = self.persistence.create_oms_order(
            intent, ticker=signal.ticker, qty=plan.get("qty"), notional=plan.get("notional"),
            limit_price=float(entry_price) if context.get("extended") else None,
        )
        if not created:
            return self._finish(self._existing_result(row))

        order = _as_order(row)
        order = self._transition(order, OrderStatus.VALIDATED)
        order = self._transition(order, OrderStatus.SUBMITTED)
        submit_kwargs: dict[str, Any] = {"client_order_id": order.client_order_id}
        if order.qty is not None:
            submit_kwargs["qty"] = order.qty
        else:
            submit_kwargs["notional"] = order.notional
        if context.get("extended"):
            submit_kwargs.update(limit_price=order.limit_price, extended_hours=True)
        transport_client = broker_client if broker_client is not None else self.transport_client
        if transport_client is not None:
            submit_kwargs["client"] = transport_client

        try:
            response = self.broker.submit_buy(order.ticker, **submit_kwargs)
        except Exception as exc:
            log.warning("OMS broker submission failed for order_id=%s: %s", order.id, type(exc).__name__)
            response = {"ok": False, "detail": f"{type(exc).__name__}: submission failed"}

        if not response.get("ok"):
            order = self._transition(order, OrderStatus.REJECTED, event_type="rejected",
                                     rejection_reason=str(response.get("detail") or "broker rejected"),
                                     payload={"detail": response.get("detail", "")})
            return self._finish(OMSResult(False, order, response.get("detail", "Broker-Ablehnung."),
                                          "broker_rejected"))

        broker_id = str(response.get("id") or "") or None
        order = self._transition(order, OrderStatus.ACCEPTED_BY_BROKER, event_type="accepted",
                                 broker_order_id=broker_id, payload=response)
        broker_status = str(response.get("status") or "accepted").lower()
        if broker_status in {"accepted", "new", "pending_new"} and broker_id and hasattr(self.broker, "get_order_status"):
            status_response = self.broker.get_order_status(
                broker_id, **({"client": transport_client} if transport_client is not None else {})
            )
            if status_response.get("ok"):
                response = status_response
                broker_status = str(status_response.get("status") or broker_status).lower()
        order = self._apply_broker_status(order, broker_status, response)
        ok = order.status not in {OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.CANCELLED}
        return self._finish(OMSResult(ok, order,
                                      "" if ok else str(response.get("detail") or broker_status),
                                      "" if ok else "broker_rejected"))

    def process_broker_event(self, order_id: int, event_type: str,
                             payload: Mapping[str, Any] | None = None,
                             *, broker_event_id: str | None = None) -> Order:
        """Apply a later accepted/fill/partial-fill/reject/cancel/expire/replace event."""
        row = self.persistence.get_oms_order(order_id)
        if row is None:
            raise KeyError(f"Unknown OMS order {order_id}")
        return self._apply_broker_status(
            _as_order(row), event_type.lower(), dict(payload or {}),
            broker_event_id=broker_event_id,
        )

    @staticmethod
    def _validate_intent(intent: TradeIntent) -> OMSResult | None:
        if not isinstance(intent, TradeIntent):
            return OMSResult(False, reason="Ungueltiger TradeIntent.", code="intent_invalid")
        if not intent.idempotency_key.strip():
            return OMSResult(False, reason="Idempotency-Key fehlt.", code="idempotency_key_missing")
        if intent.requested_action.lower() not in {"accept", "buy", "open"}:
            return OMSResult(False, reason="Nur Long-Einstiege sind erlaubt.", code="action_blocked")
        if not intent.accepted_exit_policy or not intent.source_channel or not intent.created_at:
            return OMSResult(False, reason="TradeIntent ist unvollstaendig.", code="intent_invalid")
        try:
            _parse_timestamp(intent.created_at)
        except ValueError:
            return OMSResult(False, reason="TradeIntent-Zeitstempel ist ungueltig.", code="intent_invalid")
        return None

    @staticmethod
    def _validate_signal(intent: TradeIntent, signal: Signal | None) -> OMSResult | None:
        if signal is None or signal.id != intent.signal_id:
            return OMSResult(False, reason="Signal nicht gefunden.", code="signal_not_found")
        if signal.direction.lower() != "long":
            return OMSResult(False, reason="Short-Orders sind deaktiviert.", code="short_blocked")
        if signal.mode != Mode.PAPER:
            return OMSResult(False, reason="Nur Paper-Signale duerfen ausgefuehrt werden.", code="paper_only")
        if signal.status != SignalStatus.ACCEPTED:
            return OMSResult(False, reason=f"Signalstatus {signal.status.value} ist nicht ausfuehrbar.",
                             code="signal_invalid")
        try:
            expires = _parse_timestamp(signal.expires_at)
        except ValueError:
            return OMSResult(False, reason="Signalablauf ist ungueltig.", code="signal_invalid")
        if expires and datetime.now(timezone.utc) > expires:
            return OMSResult(False, reason="Signal ist abgelaufen.", code="signal_expired")
        return None

    def _transition(self, order: Order, target: OrderStatus, **event: Any) -> Order:
        assert_order_transition(order.status, target)
        row = self.persistence.transition_oms_order(
            order.id, from_status=order.status.value, to_status=target.value, **event
        )
        return _as_order(row)

    def _apply_broker_status(self, order: Order, status: str,
                             payload: Mapping[str, Any],
                             *, broker_event_id: str | None = None) -> Order:
        normalized = {"fill": "filled", "partial_fill": "partially_filled",
                      "canceled": "cancelled"}.get(status, status)
        if normalized == "replaced":
            if order.status not in {
                OrderStatus.SUBMITTED, OrderStatus.ACCEPTED_BY_BROKER,
                OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_REQUESTED,
            }:
                raise ValueError(f"Cannot replace order in terminal status {order.status.value}")
            row = self.persistence.record_oms_order_event(
                order.id, status=order.status.value, event_type="replaced",
                broker_event_id=broker_event_id, payload=dict(payload),
                broker_order_id=(str(payload.get("broker_order_id") or payload.get("id") or "")
                                 or None),
            )
            return _as_order(row)
        target = {
            "accepted": OrderStatus.ACCEPTED_BY_BROKER,
            "new": OrderStatus.ACCEPTED_BY_BROKER,
            "pending_new": OrderStatus.ACCEPTED_BY_BROKER,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED,
            "rejected": OrderStatus.REJECTED,
            "cancelled": OrderStatus.CANCELLED,
            "expired": OrderStatus.EXPIRED,
        }.get(normalized)
        if target is None:
            return order
        if target == order.status:
            if broker_event_id:
                row = self.persistence.record_oms_order_event(
                    order.id, status=order.status.value, event_type=normalized,
                    broker_event_id=broker_event_id, payload=dict(payload),
                )
                return _as_order(row)
            return order
        if order.status == OrderStatus.SUBMITTED and target in {
            OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
        }:
            order = self._transition(order, OrderStatus.ACCEPTED_BY_BROKER,
                                     event_type="accepted", payload=dict(payload))
        if target == OrderStatus.CANCELLED and order.status in {
            OrderStatus.ACCEPTED_BY_BROKER, OrderStatus.PARTIALLY_FILLED,
        }:
            order = self._transition(order, OrderStatus.CANCEL_REQUESTED,
                                     event_type="cancel_requested", payload=dict(payload))
        return self._transition(order, target, event_type=normalized,
                                broker_event_id=broker_event_id, payload=dict(payload),
                                rejection_reason=(str(payload.get("detail") or normalized)
                                                  if target == OrderStatus.REJECTED else None))

    @staticmethod
    def _existing_result(row: Mapping[str, Any]) -> OMSResult:
        order = _as_order(row)
        ok = order.status not in {OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.CANCELLED}
        return OMSResult(ok, order, row.get("rejection_reason") or "",
                         "broker_rejected" if not ok else "", idempotent=True)

    def _finish(self, result: OMSResult) -> OMSResult:
        if self.notifier:
            try:
                self.notifier(result)
            except Exception as exc:
                log.warning("OMS notification hook failed: %s", type(exc).__name__)
        return result


OMS = OrderManagementSystem
