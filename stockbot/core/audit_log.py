"""Audit-Log append-only Store (PLAT-002, siehe docs/Plan.md §9.4, docs/PLAN_CHECKLIST.md Phase 1).

Reiner, IO-freier In-Prozess-Store für `AuditEvent` (`stockbot/core/domain.py`) — noch nicht an
eine DB gebunden und von KEINEM Live-Codepfad genutzt (die bestehende `trade_events`-Tabelle in
`stockbot/core/db.py` bleibt bis zum Postgres-Cutover das aktive Event-Log). Zweck: das
Append-only-Prinzip aus Plan.md §9.4 ("Audit-Daten werden append-only behandelt.") technisch
erzwingen, bevor die persistente Anbindung darauf aufsetzt — `AuditLog` bietet daher bewusst
keine update()/delete()-Methode.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from stockbot.core.domain import AuditEvent


def new_event_id() -> str:
    return str(uuid.uuid4())


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    """Append-only In-Prozess-Log aus `AuditEvent`-Objekten (Plan.md §9.4 Felder)."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> AuditEvent:
        """Einziger Weg, ein Event hinzuzufügen — es gibt absichtlich keine update()/delete()."""
        self._events.append(event)
        return event

    def record(
        self,
        *,
        actor: str,
        entity_type: str,
        entity_id: str,
        action: str,
        old_state: str | None,
        new_state: str | None,
        trace_id: str,
        source_channel: str,
        user_id: int | None = None,
        metadata: dict | None = None,
        event_id: str | None = None,
        timestamp: str | None = None,
    ) -> AuditEvent:
        """Baut ein `AuditEvent` (Event-ID/Timestamp per Default generiert) und hängt es an."""
        event = AuditEvent(
            event_id=event_id or new_event_id(),
            timestamp=timestamp or utcnow_iso(),
            user_id=user_id,
            actor=actor,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            old_state=old_state,
            new_state=new_state,
            trace_id=trace_id,
            source_channel=source_channel,
            metadata=metadata or {},
        )
        return self.append(event)

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        """Read-only Sicht auf alle bisher aufgezeichneten Events, in Einfügereihenfolge."""
        return tuple(self._events)

    def events_for_entity(self, entity_type: str, entity_id: str) -> tuple[AuditEvent, ...]:
        """Alle Events einer bestimmten Entität, in Einfügereihenfolge — z. B. für
        eine vollständige Ereignishistorie je Order (Gate P4)."""
        return tuple(
            e for e in self._events
            if e.entity_type == entity_type and e.entity_id == entity_id
        )

    def __len__(self) -> int:
        return len(self._events)
