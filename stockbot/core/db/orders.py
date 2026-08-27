"""OMS-Orders, Order-Events und Schutzorders (Phase 4).

Die Order-Zeile ist der Ankerpunkt gegen Doppelausführung: ``create_oms_order`` hängt an
einem Idempotenzschlüssel, ``transition_oms_order`` schaltet den Status per
Compare-and-set weiter und schreibt den Übergang als Order-Event mit.
``burn_in_order_stats`` liest daraus die Burn-in-Kennzahlen.
"""

import json
import sqlite3
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


# -- OMS persistence (Phase 4) -------------------------------------------------

def get_order_by_idempotency_key(idempotency_key: str) -> dict | None:
    """Return the one OMS order belonging to a user action, if it exists."""
    with db._database().transaction() as transaction:
        return transaction.one(
            "SELECT * FROM orders WHERE idempotency_key = :idempotency_key",
            {"idempotency_key": idempotency_key},
        )


def get_oms_order(order_id: int) -> dict | None:
    with db._database().transaction() as transaction:
        return transaction.one("SELECT * FROM orders WHERE id = :order_id", {"order_id": order_id})


def get_open_oms_orders() -> list[dict]:
    """Lädt pollbare, nicht-terminale OMS-Orders über den DB-Seam."""
    with db._database().transaction() as transaction:
        return transaction.all(
            """SELECT * FROM orders
               WHERE status IN ('submitted', 'accepted_by_broker', 'partially_filled',
                                'cancel_requested')
                 AND broker_order_id IS NOT NULL AND broker_order_id <> ''
               ORDER BY user_id, id""",
        )


def get_active_protective_orders(user_id: int, ticker: str) -> list[dict]:
    """Lädt aktive brokerseitige Schutzorders für die Partial-Fill-Dedup."""
    with db._database().transaction() as transaction:
        return transaction.all(
            """SELECT * FROM protective_orders
               WHERE user_id = :user_id AND ticker = :ticker
                 AND status IN ('submitted', 'accepted_by_broker', 'partially_filled',
                                'cancel_requested')
               ORDER BY id""",
            {"user_id": user_id, "ticker": ticker},
        )


def record_protective_order(*, source_order_id: int, trade_intent_id: int, user_id: int,
                            ticker: str, qty: float, stop_price: float,
                            broker_order_id: str) -> dict:
    """Persistiert eine platzierte Stop-Order getrennt vom Entry-OMS-Polling."""
    timestamp = db._utc_timestamp()
    with db._database().transaction() as transaction:
        existing = transaction.one(
            "SELECT * FROM protective_orders WHERE broker_order_id = :broker_order_id",
            {"broker_order_id": broker_order_id},
        )
        if existing:
            return existing
        order_id = transaction.insert_id(
            """INSERT INTO protective_orders
               (source_order_id, trade_intent_id, user_id, ticker, side, qty, stop_price,
                status, broker_order_id, created_at, updated_at)
               VALUES (:source_order_id, :trade_intent_id, :user_id, :ticker, 'sell', :qty,
                       :stop_price, 'accepted_by_broker', :broker_order_id, :created_at,
                       :updated_at)""",
            {"source_order_id": source_order_id, "trade_intent_id": trade_intent_id,
             "user_id": user_id, "ticker": ticker, "qty": qty, "stop_price": stop_price,
             "broker_order_id": broker_order_id, "created_at": timestamp,
             "updated_at": timestamp},
        )
        return transaction.one(
            "SELECT * FROM protective_orders WHERE id = :order_id", {"order_id": order_id},
        )


def get_oms_trade_intent(trade_intent_id: int) -> dict | None:
    with db._database().transaction() as transaction:
        return transaction.one(
            "SELECT * FROM trade_intents WHERE id = :trade_intent_id",
            {"trade_intent_id": trade_intent_id},
        )


def create_oms_order(intent, *, ticker: str, qty: float | None,
                     notional: float | None, limit_price: float | None = None) -> tuple[dict, bool]:
    """Persist intent and order atomically.

    The database constraints are the final concurrency guard.  If another request
    wins the same idempotency key, this call returns that order with ``False``.
    """
    try:
        with db._database().transaction() as transaction:
            existing = transaction.one(
                "SELECT * FROM orders WHERE idempotency_key = :idempotency_key",
                {"idempotency_key": intent.idempotency_key},
            )
            if existing:
                return existing, False
            intent_id = transaction.insert_id(
                """INSERT INTO trade_intents
                   (user_id, signal_id, requested_action, accepted_exit_policy,
                    source_channel, created_at, idempotency_key)
                   VALUES (:user_id, :signal_id, :requested_action, :accepted_exit_policy,
                           :source_channel, :created_at, :idempotency_key)""",
                {"user_id": intent.user_id, "signal_id": intent.signal_id,
                 "requested_action": intent.requested_action,
                 "accepted_exit_policy": intent.accepted_exit_policy,
                 "source_channel": intent.source_channel, "created_at": intent.created_at,
                 "idempotency_key": intent.idempotency_key},
            )
            order_id = transaction.insert_id(
                """INSERT INTO orders
                   (trade_intent_id, user_id, ticker, side, qty, notional,
                    limit_price, status, idempotency_key, created_at, updated_at)
                   VALUES (:intent_id, :user_id, :ticker, 'buy', :qty, :notional,
                           :limit_price, 'created', :idempotency_key, :created_at, :updated_at)""",
                {"intent_id": intent_id, "user_id": intent.user_id, "ticker": ticker,
                 "qty": qty, "notional": notional, "limit_price": limit_price,
                 "idempotency_key": intent.idempotency_key,
                 "created_at": db._utc_timestamp(), "updated_at": db._utc_timestamp()},
            )
            client_order_id = f"oms-{order_id}"
            transaction.execute(
                "UPDATE orders SET client_order_id = :client_order_id WHERE id = :order_id",
                {"client_order_id": client_order_id, "order_id": order_id},
            )
            transaction.execute(
                """INSERT INTO order_events
                   (order_id, event_type, from_status, to_status, payload_json, occurred_at)
                   VALUES (:order_id, 'created', NULL, 'created', '{}', :occurred_at)""",
                {"order_id": order_id, "occurred_at": db._utc_timestamp()},
            )
            row = transaction.one("SELECT * FROM orders WHERE id = :order_id", {"order_id": order_id})
            return row, True
    except (sqlite3.IntegrityError, SQLAlchemyIntegrityError):
        existing = get_order_by_idempotency_key(intent.idempotency_key)
        if existing is None:
            raise
        return existing, False


def transition_oms_order(order_id: int, *, from_status: str, to_status: str,
                         event_type: str | None = None, broker_order_id: str | None = None,
                         broker_event_id: str | None = None, payload: dict | None = None,
                         rejection_reason: str | None = None) -> dict:
    """Update an order and append its event in the same transaction."""
    with db._database().transaction() as transaction:
        changed = transaction.execute(
            """UPDATE orders
               SET status = :to_status, broker_order_id = COALESCE(:broker_order_id, broker_order_id),
                   rejection_reason = COALESCE(:rejection_reason, rejection_reason),
                   updated_at = :updated_at
               WHERE id = :order_id AND status = :from_status""",
            {"to_status": to_status, "broker_order_id": broker_order_id,
             "rejection_reason": rejection_reason, "updated_at": db._utc_timestamp(),
             "order_id": order_id, "from_status": from_status},
        )
        if changed != 1:
            current = transaction.one("SELECT status FROM orders WHERE id = :order_id", {"order_id": order_id})
            actual = current["status"] if current else "missing"
            raise RuntimeError(
                f"OMS order {order_id} transition race: expected {from_status}, found {actual}"
            )
        transaction.execute(
            """INSERT INTO order_events
               (order_id, event_type, from_status, to_status, broker_event_id, payload_json,
                occurred_at)
               VALUES (:order_id, :event_type, :from_status, :to_status, :broker_event_id,
                       :payload_json, :occurred_at)""",
            {"order_id": order_id, "event_type": event_type or to_status,
             "from_status": from_status, "to_status": to_status,
             "broker_event_id": broker_event_id,
             "payload_json": json.dumps(payload or {}, default=str),
             "occurred_at": db._utc_timestamp()},
        )
        row = transaction.one("SELECT * FROM orders WHERE id = :order_id", {"order_id": order_id})
    return row


def record_oms_order_event(order_id: int, *, status: str, event_type: str,
                           broker_event_id: str | None = None, payload: dict | None = None,
                           broker_order_id: str | None = None) -> dict:
    """Append a broker event which does not change the domain order status."""
    with db._database().transaction() as transaction:
        changed = transaction.execute(
            """UPDATE orders
               SET broker_order_id = COALESCE(:broker_order_id, broker_order_id),
                   updated_at = :updated_at
               WHERE id = :order_id AND status = :status""",
            {"broker_order_id": broker_order_id, "updated_at": db._utc_timestamp(),
             "order_id": order_id, "status": status},
        )
        if changed != 1:
            current = transaction.one("SELECT status FROM orders WHERE id = :order_id", {"order_id": order_id})
            actual = current["status"] if current else "missing"
            raise RuntimeError(
                f"OMS order {order_id} event race: expected {status}, found {actual}"
            )
        transaction.execute(
            """INSERT INTO order_events
               (order_id, event_type, from_status, to_status, broker_event_id, payload_json,
                occurred_at)
               VALUES (:order_id, :event_type, :status, :status, :broker_event_id,
                       :payload_json, :occurred_at)""",
            {"order_id": order_id, "event_type": event_type, "status": status,
             "broker_event_id": broker_event_id,
             "payload_json": json.dumps(payload or {}, default=str),
             "occurred_at": db._utc_timestamp()},
        )
        row = transaction.one("SELECT * FROM orders WHERE id = :order_id", {"order_id": order_id})
    return row


def get_oms_order_events(order_id: int) -> list[dict]:
    with db._database().transaction() as transaction:
        return transaction.all(
            "SELECT * FROM order_events WHERE order_id = :order_id ORDER BY id",
            {"order_id": order_id},
        )


def burn_in_order_stats(since: str, until: str) -> dict:
    """Kennzahlen des Paper-Burn-ins für den Zeitraum (naive UTC-Strings, W8/Gate P10).

    Liefert eingereichte und abgelehnte Orders, doppelte Order-Zeilen je Idempotency-Key,
    mehrfach verbuchte Broker-Events und Dead-Letter-Events. Auswertung in
    `stockbot/core/burn_in.py`.
    """
    window = {"since": since, "until": until}
    with db._database().transaction() as transaction:
        submitted = transaction.one(
            "SELECT COUNT(*) AS n FROM orders WHERE created_at BETWEEN :since AND :until",
            window)["n"]
        failed = transaction.one(
            """SELECT COUNT(*) AS n FROM orders
               WHERE created_at BETWEEN :since AND :until
                 AND status IN ('rejected', 'expired')""",
            window)["n"]
        # Der Unique-Index auf idempotency_key macht Doppel-Orders unmöglich; die Abfrage
        # bleibt trotzdem, weil sie genau diese Zusage im Report belegt.
        duplicate_orders = transaction.one(
            """SELECT COUNT(*) AS n FROM (
                   SELECT idempotency_key FROM orders
                   WHERE created_at BETWEEN :since AND :until
                   GROUP BY idempotency_key HAVING COUNT(*) > 1) d""",
            window)["n"]
        duplicate_events = transaction.one(
            """SELECT COUNT(*) AS n FROM (
                   SELECT order_id, broker_event_id FROM order_events
                   WHERE broker_event_id IS NOT NULL
                     AND occurred_at BETWEEN :since AND :until
                   GROUP BY order_id, broker_event_id HAVING COUNT(*) > 1) d""",
            window)["n"]
        dead_letters = transaction.one(
            """SELECT COUNT(*) AS n FROM outbox_events
               WHERE status = 'dead' AND updated_at BETWEEN :since AND :until""",
            window)["n"]
    return {"submitted": submitted, "failed": failed, "duplicate_orders": duplicate_orders,
            "duplicate_broker_events": duplicate_events, "dead_letter_events": dead_letters}
