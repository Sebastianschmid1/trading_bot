"""Outbox und Telegram-Callback-Tokens.

Die Outbox speichert ein Domain-Event in derselben Transaktion wie die Änderung, die es
auslöst (Parameter ``transaction=``) — entweder beides oder nichts. Ein Zustell-Worker
holt es später mit Retry und Dead-Letter ab.

Callback-Tokens ersetzen manipulierbare ``callback_data`` durch einen opaken, an einen
Nutzer gebundenen und ablaufenden Verweis, der serverseitig aufgelöst wird.
"""

import json
import secrets

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


# ── Outbox (Paket C) ─────────────────────────────────────────────────────────
# Atomare Speicherung eines Domain-Events zusammen mit der Domänenänderung (gemeinsame
# Transaktion via `transaction=`), Auslieferungs-Worker mit Retry + Dead-Letter in
# stockbot/core/outbox.py.

def enqueue_outbox_event(event, *, next_attempt_at: str | None = None,
                         max_attempts: int = 5, transaction=None) -> None:
    """Legt ein `DomainEvent` in die Outbox — optional in eine BESTEHENDE Transaktion."""
    now = db._utc_timestamp()
    params = {
        "event_id": event.event_id, "event_type": event.event_type,
        "version": event.version, "trace_id": event.trace_id,
        "payload_json": json.dumps(event.payload), "next_attempt_at": next_attempt_at or now,
        "max_attempts": max_attempts, "created_at": now, "updated_at": now,
    }
    sql = """INSERT INTO outbox_events
             (event_id, event_type, version, trace_id, payload_json, status, attempts,
              max_attempts, next_attempt_at, last_error, created_at, updated_at)
             VALUES (:event_id, :event_type, :version, :trace_id, :payload_json,
                     'pending', 0, :max_attempts, :next_attempt_at, NULL,
                     :created_at, :updated_at)"""
    if transaction is not None:
        transaction.execute(sql, params)
    else:
        with db._database().transaction() as tx:
            tx.execute(sql, params)


def fetch_due_outbox_events(now: str, limit: int = 100) -> list[dict]:
    """Zustellbereite Events (pending und `next_attempt_at` ≤ now), älteste zuerst."""
    with db._database().transaction() as tx:
        rows = tx.all(
            """SELECT id, event_id, event_type, version, trace_id, payload_json,
                      attempts, max_attempts
               FROM outbox_events
               WHERE status = 'pending' AND next_attempt_at <= :now
               ORDER BY id LIMIT :limit""",
            {"now": now, "limit": limit},
        )
    return [dict(r) for r in rows]


def mark_outbox_delivered(event_id: str) -> None:
    with db._database().transaction() as tx:
        tx.execute("UPDATE outbox_events SET status = 'delivered', updated_at = :now "
                   "WHERE event_id = :eid", {"now": db._utc_timestamp(), "eid": event_id})


def mark_outbox_retry(event_id: str, next_attempt_at: str, last_error: str) -> None:
    with db._database().transaction() as tx:
        tx.execute(
            """UPDATE outbox_events SET attempts = attempts + 1, next_attempt_at = :next,
                      last_error = :err, updated_at = :now WHERE event_id = :eid""",
            {"next": next_attempt_at, "err": last_error, "now": db._utc_timestamp(),
             "eid": event_id})


def mark_outbox_dead(event_id: str, last_error: str) -> None:
    """Dead-Letter: nach erschöpften Versuchen dauerhaft aus der Zustellung nehmen."""
    with db._database().transaction() as tx:
        tx.execute(
            """UPDATE outbox_events SET status = 'dead', attempts = attempts + 1,
                      last_error = :err, updated_at = :now WHERE event_id = :eid""",
            {"err": last_error, "now": db._utc_timestamp(), "eid": event_id})


def outbox_backlog_count() -> int:
    """Anzahl noch nicht zugestellter Events (für Rückstand-Monitoring)."""
    with db._database().transaction() as tx:
        row = tx.one("SELECT COUNT(*) AS n FROM outbox_events WHERE status = 'pending'", {})
    return int(row["n"]) if row else 0


# ── Telegram-Callback-Sicherheit (W7) ────────────────────────────────────────
# Opaque, serverseitig aufgelöste Tokens statt manipulierbarer callback_data: nutzergebunden,
# mit Ablauf und EINMALIGER Verwendung (idempotent gegen Doppel-Callback/Replay).

def issue_callback_token(*, user_id: int, action: str, payload: dict, expires_at: str) -> str:
    """Erzeugt ein opaques, nutzergebundenes Token für eine Aktion und gibt es zurück."""
    token = secrets.token_urlsafe(24)
    now = db._utc_timestamp()
    with db._database().transaction() as tx:
        tx.execute(
            """INSERT INTO callback_tokens
               (token, user_id, action, payload_json, expires_at, used_at, created_at)
               VALUES (:token, :user_id, :action, :payload_json, :expires_at, NULL, :created_at)""",
            {"token": token, "user_id": user_id, "action": action,
             "payload_json": json.dumps(payload), "expires_at": expires_at, "created_at": now},
        )
    return token


def resolve_callback_token(token: str, user_id: int, now: str):
    """Löst ein Callback-Token EINMALIG auf → (ok, action, payload, reason).

    Prüft in EINER Transaktion: existiert / nicht abgelaufen / an user_id gebunden / noch nicht
    verwendet; markiert es bei Erfolg als verwendet (one-time, idempotenzsicher)."""
    with db._database().transaction() as tx:
        row = tx.one(
            "SELECT user_id, action, payload_json, expires_at, used_at "
            "FROM callback_tokens WHERE token = :token", {"token": token})
        if not row:
            return (False, None, None, "unknown")
        if row["used_at"] is not None:
            return (False, None, None, "used")
        if row["expires_at"] <= now:
            return (False, None, None, "expired")
        if int(row["user_id"]) != int(user_id):
            return (False, None, None, "wrong_user")
        tx.execute(
            "UPDATE callback_tokens SET used_at = :now WHERE token = :token AND used_at IS NULL",
            {"now": now, "token": token})
        return (True, row["action"], json.loads(row["payload_json"]), "")


def purge_expired_callback_tokens(now: str) -> None:
    with db._database().transaction() as tx:
        tx.execute("DELETE FROM callback_tokens WHERE expires_at <= :now", {"now": now})
