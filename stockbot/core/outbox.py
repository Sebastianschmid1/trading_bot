"""Outbox-Auslieferung mit Retry + Dead-Letter + Rückstand-Monitoring (Paket C).

Der Worker liest fällige Events aus der Outbox und stellt sie an idempotente Consumer zu.
Erfolg → `delivered`; Fehler → Retry mit exponentiellem Backoff, bis das Versuchsbudget
erschöpft ist → Dead-Letter (`dead`). Die Zustellung darf den Worker nie abbrechen.

**Idempotenz:** Schlägt EIN Consumer fehl, wird das GESAMTE Event später erneut zugestellt —
bereits erfolgreiche Consumer sehen es dann nochmal. Consumer müssen daher idempotent sein
(über die eindeutige `event_id`, siehe `event_consumers.DedupConsumer`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from stockbot.core import db

log = logging.getLogger(__name__)

DEFAULT_BACKOFF_BASE_SEC = 30
BACKOFF_CAP_SEC = 3600


def exponential_backoff(attempts: int, base: int = DEFAULT_BACKOFF_BASE_SEC,
                        cap: int = BACKOFF_CAP_SEC) -> int:
    """Exponentieller Backoff in Sekunden, gedeckelt bei `cap`."""
    return min(cap, base * (2 ** max(0, attempts)))


@dataclass
class DeliveryResult:
    delivered: int = 0
    retried: int = 0
    dead: int = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def deliver_due(consumers, *, now: datetime | None = None, limit: int = 100,
                backoff=exponential_backoff, persistence=db) -> DeliveryResult:
    """Stellt fällige Outbox-Events an alle `consumers` zu (idempotent erwartet)."""
    now = now or _now()
    result = DeliveryResult()
    for row in persistence.fetch_due_outbox_events(_fmt(now), limit=limit):
        event = {
            "event_id": row["event_id"], "event_type": row["event_type"],
            "version": row["version"], "trace_id": row["trace_id"],
            "payload": json.loads(row["payload_json"]),
        }
        try:
            for consumer in consumers:
                consumer.handle(event)
        except Exception as exc:  # noqa: BLE001 — Zustellfehler darf den Worker nicht killen
            attempts_after = row["attempts"] + 1
            err = f"{type(exc).__name__}: {exc}"
            if attempts_after >= row["max_attempts"]:
                persistence.mark_outbox_dead(row["event_id"], err)
                result.dead += 1
                log.error("Outbox-Event %s dead-lettered nach %d Versuchen: %s",
                          row["event_id"], attempts_after, err)
            else:
                next_at = _fmt(now + timedelta(seconds=backoff(attempts_after)))
                persistence.mark_outbox_retry(row["event_id"], next_at, err)
                result.retried += 1
        else:
            persistence.mark_outbox_delivered(row["event_id"])
            result.delivered += 1
    return result


def backlog_count(persistence=db) -> int:
    """Rückstand: Anzahl noch nicht zugestellter Events (für Monitoring/Alarme, s. W4.2)."""
    return persistence.outbox_backlog_count()
