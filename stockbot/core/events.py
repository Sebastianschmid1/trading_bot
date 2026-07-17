"""Versioniertes Domain-Event-Set (Paket B).

Ein kleiner, IO-freier Kern: jede Zustandsänderung im System kann als versioniertes
`DomainEvent` ausgedrückt werden. Die Version je Event-Typ ist zentral registriert
(`EVENT_SCHEMA`) — ändert sich das Payload-Schema, wird die Version erhöht, damit Consumer
alte und neue Formate unterscheiden können. Jedes Event trägt eine `trace_id` für
durchgängige Nachvollziehbarkeit (konsistent mit `logging_setup`/Audit-Log).

Consumer sind idempotent über die eindeutige `event_id` (siehe `outbox`/Paket C).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Bekannte Event-Typen + ihre aktuelle Schema-Version (Plan.md Paket B).
SIGNAL_GENERATED = "SignalGenerated"
ORDER_SUBMITTED = "OrderSubmitted"
ORDER_FILLED = "OrderFilled"
ORDER_REJECTED = "OrderRejected"
ORDER_CANCELLED = "OrderCancelled"
PARTIAL_FILL = "PartialFill"
RECONCILIATION_FINDING = "ReconciliationFinding"
KILL_SWITCH_ACTIVATED = "KillSwitchActivated"
KILL_SWITCH_DEACTIVATED = "KillSwitchDeactivated"

EVENT_SCHEMA: dict[str, int] = {
    SIGNAL_GENERATED: 1,
    ORDER_SUBMITTED: 1,
    ORDER_FILLED: 1,
    ORDER_REJECTED: 1,
    ORDER_CANCELLED: 1,
    PARTIAL_FILL: 1,
    RECONCILIATION_FINDING: 1,
    KILL_SWITCH_ACTIVATED: 1,
    KILL_SWITCH_DEACTIVATED: 1,
}


class UnknownEventType(ValueError):
    """Der Event-Typ ist nicht im `EVENT_SCHEMA` registriert."""


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class DomainEvent:
    """Ein versioniertes Domänen-Ereignis mit Trace-ID und eindeutiger ID."""

    event_type: str
    version: int
    payload: dict
    trace_id: str
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: str = field(default_factory=_utc_now_str)

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "version": self.version,
            "trace_id": self.trace_id,
            "occurred_at": self.occurred_at,
            "payload": dict(self.payload),
        }


def make_event(event_type: str, payload: dict | None = None, *, trace_id: str,
               event_id: str | None = None) -> DomainEvent:
    """Erzeugt ein `DomainEvent` und stempelt die registrierte Version des Typs auf.

    `trace_id` ist Pflicht (durchgängige Nachvollziehbarkeit). Unbekannte Typen werden
    abgelehnt, damit kein unversioniertes Event in die Outbox gelangt.
    """
    if event_type not in EVENT_SCHEMA:
        raise UnknownEventType(f"unbekannter Event-Typ: {event_type!r}")
    if not trace_id:
        raise ValueError("trace_id ist Pflicht")
    kwargs = {} if event_id is None else {"event_id": event_id}
    return DomainEvent(
        event_type=event_type, version=EVENT_SCHEMA[event_type],
        payload=dict(payload or {}), trace_id=trace_id, **kwargs,
    )
