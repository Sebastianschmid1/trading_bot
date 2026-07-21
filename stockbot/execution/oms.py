"""Idempotent order-management pipeline for long-only entries.

Web and Telegram hand a :class:`TradeIntent` to this service.  Broker access,
notification delivery and signal/context loading are injected so this module has
no dependency on either UI channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable, Mapping

from stockbot import config
from stockbot.broker import client as default_broker
from stockbot.broker import sizing
from stockbot.core import db, events, metrics, risk
from stockbot.core.audit_log import new_event_id
from stockbot.core.logging_setup import logging_context, new_trace_id
from stockbot.core.domain import AuditEvent, Mode, Order, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.core.state_machine import assert_order_transition, is_terminal_order_status

log = logging.getLogger(__name__)

# Welcher Orderzustand welches Domain-Event auslöst (W4.5). Zwischenschritte wie `created`
# oder `validated` sind bewusst nicht dabei: nach außen zählt, was der Broker bestätigt hat.
_STATUS_EVENT_TYPES = {
    OrderStatus.SUBMITTED: events.ORDER_SUBMITTED,
    OrderStatus.FILLED: events.ORDER_FILLED,
    OrderStatus.PARTIALLY_FILLED: events.PARTIAL_FILL,
    OrderStatus.REJECTED: events.ORDER_REJECTED,
    OrderStatus.CANCELLED: events.ORDER_CANCELLED,
}


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


def _as_utc(value: Any) -> datetime | None:
    """Normalisiert eine Callsite-Zeitbasis auf tz-aware UTC; alles Unbrauchbare → None."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _as_order(row: Mapping[str, Any], *, mode: Mode = Mode.PAPER) -> Order:
    """Map a legacy SQLite row to an Order without migrating SQLite for RES-002.

    Existing SQLite rows are Paper unless the caller carries a known domain mode.
    The persistent mode column follows with the documented Postgres cutover.
    """
    return Order(
        id=int(row["id"]), trade_intent_id=int(row["trade_intent_id"]),
        user_id=int(row["user_id"]), ticker=str(row["ticker"]), side=str(row["side"]),
        mode=Mode(row.get("mode", mode)),
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
        audit_sink: Callable[[AuditEvent], Any] | None = None,
        kill_switch_checker: Callable[[int], bool] | None = None,
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
        self.audit_sink = audit_sink
        self.kill_switch_checker = kill_switch_checker
        self.persistence = persistence
        self._audit_contexts: dict[int, tuple[str, str, str]] = {}

    def submit_intent(
        self, intent: TradeIntent, *, price: float | None = None,
        trade_size: float | None = None, leverage: float = 1.0,
        risk_context: Mapping[str, Any] | None = None,
        broker_client: Any = None,
    ) -> OMSResult:
        """Validate, risk-check, persist and submit one user action."""
        started_at = time.perf_counter()
        finish = lambda result: self._finish_submission(result, started_at)
        invalid = self._validate_intent(intent)
        if invalid:
            return finish(invalid)

        existing = self.persistence.get_order_by_idempotency_key(intent.idempotency_key)
        if existing:
            return finish(self._existing_result(existing))

        # Kill-Switch-Gate NACH dem Idempotenz-Check: ein idempotenter Retry einer bereits
        # platzierten Order ist keine NEUE Position und muss die existierende Order weiter
        # zurueckgeben; der Switch blockt nur genuin neue Einstiege.
        if self.kill_switch_checker is not None and not self.kill_switch_checker(intent.user_id):
            return finish(OMSResult(
                False, reason="Kill-Switch aktiv — keine neuen Positionen.",
                code="kill_switch_active",
            ))

        # Die Callsite darf ihre Zeitbasis mitgeben (`now` im Risk-Kontext). Ohne Angabe
        # bleibt es die Wanduhr — Prod verhaelt sich unveraendert, Tests werden deterministisch.
        now = _as_utc(risk_context.get("now") if risk_context else None) or datetime.now(timezone.utc)

        signal = self.signal_loader(intent.signal_id)
        invalid = self._validate_signal(intent, signal, now=now)
        if invalid:
            return finish(invalid)
        assert signal is not None

        context: dict[str, Any] = {}
        if self.context_loader:
            with metrics.observe_latency(metrics.FEED_LATENCY):
                context.update(self.context_loader(intent, signal) or {})
        context.update(risk_context or {})
        quote_as_of = getattr(context.get("quote"), "as_of", None)
        if quote_as_of is not None:
            try:  # Metrik darf den Live-Order-Pfad nie brechen (untypische as_of-Werte)
                if quote_as_of.tzinfo is None:
                    quote_as_of = quote_as_of.replace(tzinfo=timezone.utc)
                metrics.QUOTE_AGE.observe(max(0.0, (now - quote_as_of).total_seconds()))
            except (AttributeError, TypeError, ValueError):
                pass
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
            return finish(OMSResult(False, reason=decision.reason, code=decision.code))

        entry_price = context.get("price")
        budget = context.get("trade_size")
        if entry_price is None or budget is None:
            return finish(OMSResult(False, reason="Kurs und Trade-Budget fehlen.",
                                          code="order_context_missing"))
        plan = self.order_planner(
            float(entry_price), float(budget), float(context.get("leverage", 1.0)),
            option_selector=None, extended=bool(context.get("extended", False)),
            roundup_factor=float(context.get("roundup_factor", config.SHARE_ROUNDUP_FACTOR)),
        )
        if plan.get("kind") != "shares":
            return finish(OMSResult(False, reason=plan.get("reason", "Kein gueltiger Aktien-Orderplan."),
                                          code="order_plan_rejected"))

        row, created = self.persistence.create_oms_order(
            intent, ticker=signal.ticker, qty=plan.get("qty"), notional=plan.get("notional"),
            limit_price=float(entry_price) if context.get("extended") else None,
        )
        if not created:
            return finish(self._existing_result(row))

        order = _as_order(row, mode=signal.mode)
        self._audit_contexts[order.id] = (
            f"user:{intent.user_id}", intent.source_channel, intent.idempotency_key,
        )
        order = self._transition(order, OrderStatus.VALIDATED)
        order = self._transition(order, OrderStatus.SUBMITTED)
        metrics.ORDERS_TOTAL.inc(outcome="submitted")
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

        with logging_context(trace_id=new_trace_id(), user_id=intent.user_id):
            try:
                response = self.broker.submit_buy(order.ticker, **submit_kwargs)
            except Exception as exc:
                log.warning(
                    "OMS broker submission failed: %s", type(exc).__name__,
                    extra={"entity_id": order.id, "event_type": "broker_submission_failed"},
                )
                response = {"ok": False, "detail": f"{type(exc).__name__}: submission failed"}

        if not response.get("ok"):
            order = self._transition(order, OrderStatus.REJECTED, event_type="rejected",
                                     rejection_reason=str(response.get("detail") or "broker rejected"),
                                     payload={"detail": response.get("detail", "")})
            metrics.ORDERS_TOTAL.inc(outcome="rejected")
            return finish(OMSResult(False, order, response.get("detail", "Broker-Ablehnung."),
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
        return finish(OMSResult(ok, order,
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
    def _validate_signal(intent: TradeIntent, signal: Signal | None,
                         *, now: datetime | None = None) -> OMSResult | None:
        if signal is None or signal.id != intent.signal_id:
            return OMSResult(False, reason="Signal nicht gefunden.", code="signal_not_found")
        if signal.direction.lower() != "long":
            return OMSResult(False, reason="Short-Orders sind deaktiviert.", code="short_blocked")
        if signal.mode != Mode.PAPER:
            return OMSResult(False, reason="Nur Paper-Signale duerfen ausgefuehrt werden.", code="paper_only")
        if intent.mode != signal.mode:
            return OMSResult(False, reason="Intent- und Signalmodus stimmen nicht ueberein.",
                             code="mode_mismatch")
        if signal.status != SignalStatus.ACCEPTED:
            return OMSResult(False, reason=f"Signalstatus {signal.status.value} ist nicht ausfuehrbar.",
                             code="signal_invalid")
        try:
            expires = _parse_timestamp(signal.expires_at)
        except ValueError:
            return OMSResult(False, reason="Signalablauf ist ungueltig.", code="signal_invalid")
        if expires and (now or datetime.now(timezone.utc)) > expires:
            return OMSResult(False, reason="Signal ist abgelaufen.", code="signal_expired")
        return None

    def _transition(self, order: Order, target: OrderStatus, **event: Any) -> Order:
        previous = order.status
        assert_order_transition(order.status, target)
        row = self.persistence.transition_oms_order(
            order.id, from_status=order.status.value, to_status=target.value, **event
        )
        transitioned = _as_order(row, mode=order.mode)
        self._audit_transition(transitioned, previous, target, event)
        self._emit_domain_event(transitioned, target, event)
        # Erreicht die Order einen Endzustand, faellt ihr Audit-Kontext raus — sonst wuechse
        # der Cache im langlebigen OMS-Singleton unbegrenzt mit. Er ist ohnehin nur ein
        # Read-through-Cache: ein spaeteres Event laedt den Intent wieder aus der Persistenz.
        if is_terminal_order_status(target):
            self._audit_contexts.pop(order.id, None)
        return transitioned

    def _emit_domain_event(self, order: Order, target: OrderStatus,
                           metadata: Mapping[str, Any]) -> None:
        """Schreibt den Statuswechsel als versioniertes Domain-Event in die Outbox.

        Fail-open wie das Audit: die Transition ist bereits persistiert, ein Fehler beim
        Einreihen darf den Handelspfad nicht brechen. Nur Zustände mit registriertem Event-Typ
        werden gemeldet — Zwischenschritte (`created`/`validated`) erzeugen keins.
        """
        event_type = _STATUS_EVENT_TYPES.get(target)
        if event_type is None:
            return
        enqueue = getattr(self.persistence, "enqueue_outbox_event", None)
        if enqueue is None:            # Test-Doubles ohne Outbox: still überspringen
            return
        context = self._audit_contexts.get(order.id)
        trace_id = (context[2] if context else None) or order.idempotency_key or f"order:{order.id}"
        try:
            enqueue(events.make_event(
                event_type,
                {
                    "order_id": order.id, "user_id": order.user_id, "ticker": order.ticker,
                    "qty": order.qty, "mode": order.mode.value,
                    "reason": metadata.get("reason"), "price": metadata.get("filled_avg_price"),
                },
                trace_id=trace_id,
            ))
        except Exception as exc:                                     # pragma: no cover - Schutznetz
            log.warning("Domain-Event nicht eingereiht (order=%s, %s): %s", order.id, target, exc)

    def _audit_transition(self, order: Order, previous: OrderStatus, target: OrderStatus,
                          metadata: Mapping[str, Any], *, action: str = "state_transition") -> None:
        if self.audit_sink is None:
            return
        context = self._audit_contexts.get(order.id)
        if context is None and hasattr(self.persistence, "get_oms_trade_intent"):
            intent = self.persistence.get_oms_trade_intent(order.trade_intent_id)
            if intent:
                context = (f"user:{intent['user_id']}", intent["source_channel"],
                           intent["idempotency_key"])
                self._audit_contexts[order.id] = context
        actor, source_channel, trace_id = context or (
            f"user:{order.user_id}", "oms", order.idempotency_key or f"order:{order.id}",
        )
        # Audit ist fail-open: die Order-Transition ist bereits persistiert; ein Fehler im
        # Audit-Sink (z. B. DB-Hiccup) darf den Trading-Pfad nicht brechen oder eine bereits
        # erfolgte Transition als Fehler zurueckmelden. Nur loggen, nicht propagieren.
        try:
            self.audit_sink(AuditEvent(
                event_id=new_event_id(), timestamp=db._utc_timestamp(), user_id=order.user_id,
                actor=actor, entity_type="order", entity_id=str(order.id), action=action,
                old_state=previous.value, new_state=target.value, trace_id=trace_id,
                source_channel=source_channel, metadata=dict(metadata),
            ))
        except Exception as exc:  # pragma: no cover - defensiver Audit-Schutz
            log.warning("OMS audit_sink failed for order_id=%s action=%s: %s",
                        order.id, action, type(exc).__name__)

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
            recorded = _as_order(row, mode=order.mode)
            self._audit_transition(recorded, order.status, order.status, payload,
                                   action="broker_event:replaced")
            return recorded
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
                recorded = _as_order(row, mode=order.mode)
                self._audit_transition(recorded, order.status, order.status, payload,
                                       action=f"broker_event:{normalized}")
                return recorded
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
        transitioned = self._transition(order, target, event_type=normalized,
                                broker_event_id=broker_event_id, payload=dict(payload),
                                rejection_reason=(str(payload.get("detail") or normalized)
                                                  if target == OrderStatus.REJECTED else None))
        if target == OrderStatus.FILLED:
            metrics.ORDERS_TOTAL.inc(outcome="filled")
        elif target == OrderStatus.REJECTED:
            metrics.ORDERS_TOTAL.inc(outcome="rejected")
        return transitioned

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

    def _finish_submission(self, result: OMSResult, started_at: float) -> OMSResult:
        metrics.ORDER_LATENCY.observe(time.perf_counter() - started_at)
        return self._finish(result)


OMS = OrderManagementSystem
