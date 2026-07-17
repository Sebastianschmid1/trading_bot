"""Event-Consumer + Notification-Templates (Paket D).

Consumer sind idempotent (Dedup über `event_id`). Der `NotificationConsumer` FORMATIERT nur und
delegiert an einen injizierten Notifier — **keine Handelslogik im Notification-Code**. Templates
nach Stylekonzept.md §26 (Modus-Präfix, keine Raketen-/Feuer-Emojis).
"""

from __future__ import annotations

import logging

from stockbot.core import events

log = logging.getLogger(__name__)


class DedupConsumer:
    """Umschließt einen Consumer und überspringt bereits erfolgreich gesehene `event_id`s."""

    def __init__(self, inner, seen=None):
        self._inner = inner
        self._seen: set[str] = set(seen or ())

    def handle(self, event: dict) -> None:
        eid = event["event_id"]
        if eid in self._seen:
            return
        self._inner.handle(event)          # erst bei Erfolg als gesehen markieren
        self._seen.add(eid)

    @property
    def seen_count(self) -> int:
        return len(self._seen)


def render_notification(event: dict) -> str:
    """Formatiert ein Event als nutzerlesbare Nachricht (Stylekonzept §26, sachlich)."""
    etype = event["event_type"]
    p = event.get("payload", {})
    prefix = f"[{str(p.get('mode', 'PAPER')).upper()}]"
    if etype == events.SIGNAL_GENERATED:
        return f"{prefix} Neues Signal: {p.get('ticker', '?')} ({p.get('strategy', '?')})"
    if etype == events.ORDER_SUBMITTED:
        return f"{prefix} Order gesendet: {p.get('ticker', '?')} {p.get('qty', '?')}"
    if etype == events.ORDER_FILLED:
        return f"{prefix} Order ausgeführt: {p.get('ticker', '?')} @ {p.get('price', '?')}"
    if etype == events.ORDER_REJECTED:
        return f"{prefix} Order abgelehnt: {p.get('ticker', '?')} — {p.get('reason', '?')}"
    if etype == events.PARTIAL_FILL:
        return (f"{prefix} Teilausführung: {p.get('ticker', '?')} "
                f"{p.get('filled_qty', '?')}/{p.get('qty', '?')}")
    if etype == events.KILL_SWITCH_ACTIVATED:
        return f"{prefix} Kill-Switch AKTIV — keine neuen Positionen."
    if etype == events.KILL_SWITCH_DEACTIVATED:
        return f"{prefix} Kill-Switch aufgehoben."
    return f"{prefix} {etype}"


class NotificationConsumer:
    """Formatiert Events und reicht sie an einen injizierten Notifier weiter (keine Handelslogik)."""

    def __init__(self, notifier, renderer=render_notification):
        self._notifier = notifier
        self._render = renderer

    def handle(self, event: dict) -> None:
        # Wirft der Notifier, propagiert der Fehler → die Outbox retryt (mit Dedup idempotent).
        self._notifier(self._render(event))
