"""Zentrale Zustandsübergangs-Validierung (Phase 1, siehe Plan.md §9.3, docs/PLAN_CHECKLIST.md).

Reine, deterministische Prüfung: erlaubt Zustand X einen Wechsel nach Y? Ob ein Übergang
inhaltlich gerechtfertigt ist (z. B. Risk-Check bestanden), entscheiden die aufrufenden
Services (Risk Service Phase 3, OMS Phase 4) — hier wird nur die Struktur der Zustandsmaschine
durchgesetzt. Noch von keinem Live-Codepfad genutzt (SQLite/`db.py` bleibt bis zum Cutover
maßgeblich).

`assert_transition`/`transition_allowed` am Ende der Datei sind der EINE zentrale Einstiegspunkt
für alle drei Entitäten (Plan.md §9.3: "Übergänge werden zentral validiert.") — er dispatcht anhand
des konkreten Enum-Typs von `from_status` an die passende Zustandsmaschine, statt dass Aufrufer
(künftig OMS/Risk Service) selbst wissen müssen, welche der drei `assert_*_transition`-Funktionen
zuständig ist. Die typisierten `assert_signal_transition`/`assert_order_transition`/
`assert_position_transition` bleiben für Aufrufer erhalten, die den Entity-Typ bereits kennen.

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

## Position (Plan.md §9.3)

    pending_open  -> {open, reconciliation_required}
    open          -> {pending_close, reconciliation_required}
    pending_close -> {closed, reconciliation_required}
    reconciliation_required -> {open, closed}

`reconciliation_required` ist kein normaler Zwischenschritt, sondern der
Abzweig für jede Phase, in der lokaler und Broker-Zustand auseinanderlaufen
(Reconciliation, Phase 4/OMS-007) — von dort aus wird nach Klärung entweder
`open` (Position besteht doch) oder `closed` (Position ist tatsächlich zu)
gesetzt. Terminal: closed.
"""

from __future__ import annotations

from stockbot.core.domain import OrderStatus, PositionStatus, SignalStatus

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


def is_terminal_order_status(status: OrderStatus) -> bool:
    """True, wenn aus `status` kein Übergang mehr herausführt (filled/cancelled/rejected/expired)."""
    return not ORDER_TRANSITIONS.get(status, frozenset())


def assert_order_transition(from_status: OrderStatus, to_status: OrderStatus) -> None:
    """Wirft `ValueError` bei einem ungültigen Order-Übergang."""
    if not order_transition_allowed(from_status, to_status):
        raise ValueError(
            f"Ungültiger Order-Übergang: {from_status.value} -> {to_status.value}"
        )


POSITION_TRANSITIONS: dict[PositionStatus, frozenset[PositionStatus]] = {
    PositionStatus.PENDING_OPEN: frozenset({
        PositionStatus.OPEN, PositionStatus.RECONCILIATION_REQUIRED,
    }),
    PositionStatus.OPEN: frozenset({
        PositionStatus.PENDING_CLOSE, PositionStatus.RECONCILIATION_REQUIRED,
    }),
    PositionStatus.PENDING_CLOSE: frozenset({
        PositionStatus.CLOSED, PositionStatus.RECONCILIATION_REQUIRED,
    }),
    PositionStatus.RECONCILIATION_REQUIRED: frozenset({
        PositionStatus.OPEN, PositionStatus.CLOSED,
    }),
    PositionStatus.CLOSED: frozenset(),
}


def position_transition_allowed(from_status: PositionStatus, to_status: PositionStatus) -> bool:
    """True, wenn `from_status -> to_status` in der Position-Zustandsmaschine erlaubt ist."""
    return to_status in POSITION_TRANSITIONS.get(from_status, frozenset())


def assert_position_transition(from_status: PositionStatus, to_status: PositionStatus) -> None:
    """Wirft `ValueError` bei einem ungültigen Position-Übergang."""
    if not position_transition_allowed(from_status, to_status):
        raise ValueError(
            f"Ungültiger Position-Übergang: {from_status.value} -> {to_status.value}"
        )


_STATUS_TYPE_TO_CHECK = {
    SignalStatus: signal_transition_allowed,
    OrderStatus: order_transition_allowed,
    PositionStatus: position_transition_allowed,
}


def transition_allowed(from_status, to_status) -> bool:
    """Zentraler Einstiegspunkt: dispatcht anhand des Enum-Typs von `from_status`
    an Signal-/Order-/Position-Zustandsmaschine. `from_status` und `to_status`
    müssen demselben Entity-Typ angehören."""
    check = _STATUS_TYPE_TO_CHECK.get(type(from_status))
    if check is None:
        raise TypeError(f"Unbekannter Zustandstyp: {type(from_status).__name__}")
    if type(to_status) is not type(from_status):
        raise TypeError(
            f"Zustandstyp-Mismatch: {type(from_status).__name__} -> {type(to_status).__name__}"
        )
    return check(from_status, to_status)


def assert_transition(from_status, to_status) -> None:
    """Wirft `ValueError` bei einem ungültigen Übergang, `TypeError` bei
    unbekanntem/gemischtem Zustandstyp. Zentraler Einstiegspunkt für alle drei
    Zustandsmaschinen (Plan.md §9.3: "Übergänge werden zentral validiert.")."""
    if not transition_allowed(from_status, to_status):
        raise ValueError(
            f"Ungültiger {type(from_status).__name__}-Übergang: "
            f"{from_status.value} -> {to_status.value}"
        )
