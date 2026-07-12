"""Zentrale Zustandsübergangs-Validierung (Phase 1, siehe Plan.md §9.3, docs/PLAN_CHECKLIST.md).

Reine, deterministische Prüfung: erlaubt Zustand X einen Wechsel nach Y? Ob ein Übergang
inhaltlich gerechtfertigt ist (z. B. Risk-Check bestanden), entscheiden die aufrufenden
Services (Risk Service Phase 3, OMS Phase 4) — hier wird nur die Struktur der Zustandsmaschine
durchgesetzt. Noch von keinem Live-Codepfad genutzt (SQLite/`db.py` bleibt bis zum Cutover
maßgeblich); dient als Grundlage für "Zentrale Validierung: ungültige Zustandsübergänge
werden abgelehnt" (nächster Checklisten-Punkt).

## Signal (Plan.md §9.3)

    generated -> filtered -> published -> {accepted, rejected, expired, blocked_by_risk}
    accepted  -> order_created

`filtered` kann direkt nach `rejected`/`expired` wechseln, wenn der Filter das Signal
aussortiert, ohne es zu veröffentlichen. Terminal: rejected, expired, blocked_by_risk,
order_created.

## Order (Plan.md §9.3)

    created   -> validated -> submitted -> accepted_by_broker
    accepted_by_broker -> {partially_filled, filled, cancel_requested, rejected, expired}
    partially_filled   -> {filled, cancel_requested, rejected, expired}
    cancel_requested   -> {cancelled, partially_filled, filled}

`created`/`validated`/`submitted` können bereits vor Brokerannahme scheitern
(`rejected`) — z. B. bei lokaler Validierung oder sofortiger Broker-Ablehnung.
Ein Cancel-Request kann mit einem in-flight Fill kollidieren: der Broker füllt
(teilweise) noch, bevor die Stornierung wirksam wird — daher erlaubt
`cancel_requested` auch `partially_filled`/`filled`, nicht nur `cancelled`.
Terminal: filled, cancelled, rejected, expired.
"""

from __future__ import annotations

from stockbot.core.domain import OrderStatus, SignalStatus

SIGNAL_TRANSITIONS: dict[SignalStatus, frozenset[SignalStatus]] = {
    SignalStatus.GENERATED: frozenset({SignalStatus.FILTERED}),
    SignalStatus.FILTERED: frozenset({
        SignalStatus.PUBLISHED, SignalStatus.REJECTED, SignalStatus.EXPIRED,
    }),
    SignalStatus.PUBLISHED: frozenset({
        SignalStatus.ACCEPTED, SignalStatus.REJECTED, SignalStatus.EXPIRED,
        SignalStatus.BLOCKED_BY_RISK,
    }),
    SignalStatus.ACCEPTED: frozenset({SignalStatus.ORDER_CREATED}),
    SignalStatus.REJECTED: frozenset(),
    SignalStatus.EXPIRED: frozenset(),
    SignalStatus.BLOCKED_BY_RISK: frozenset(),
    SignalStatus.ORDER_CREATED: frozenset(),
}


def signal_transition_allowed(from_status: SignalStatus, to_status: SignalStatus) -> bool:
    """True, wenn `from_status -> to_status` in der Signal-Zustandsmaschine erlaubt ist."""
    return to_status in SIGNAL_TRANSITIONS.get(from_status, frozenset())


def assert_signal_transition(from_status: SignalStatus, to_status: SignalStatus) -> None:
    """Wirft `ValueError` bei einem ungültigen Signal-Übergang."""
    if not signal_transition_allowed(from_status, to_status):
        raise ValueError(
            f"Ungültiger Signal-Übergang: {from_status.value} -> {to_status.value}"
        )


ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.VALIDATED, OrderStatus.REJECTED}),
    OrderStatus.VALIDATED: frozenset({OrderStatus.SUBMITTED, OrderStatus.REJECTED}),
    OrderStatus.SUBMITTED: frozenset({
        OrderStatus.ACCEPTED_BY_BROKER, OrderStatus.REJECTED, OrderStatus.EXPIRED,
    }),
    OrderStatus.ACCEPTED_BY_BROKER: frozenset({
        OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_REQUESTED,
        OrderStatus.REJECTED, OrderStatus.EXPIRED,
    }),
    OrderStatus.PARTIALLY_FILLED: frozenset({
        OrderStatus.FILLED, OrderStatus.CANCEL_REQUESTED, OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }),
    OrderStatus.CANCEL_REQUESTED: frozenset({
        OrderStatus.CANCELLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
    }),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


def order_transition_allowed(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    """True, wenn `from_status -> to_status` in der Order-Zustandsmaschine erlaubt ist."""
    return to_status in ORDER_TRANSITIONS.get(from_status, frozenset())


def assert_order_transition(from_status: OrderStatus, to_status: OrderStatus) -> None:
    """Wirft `ValueError` bei einem ungültigen Order-Übergang."""
    if not order_transition_allowed(from_status, to_status):
        raise ValueError(
            f"Ungültiger Order-Übergang: {from_status.value} -> {to_status.value}"
        )
