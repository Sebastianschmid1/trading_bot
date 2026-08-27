"""Trade-Abfragen — der Lesepfad.

Projektionen auf die ``trades``-Tabelle: offene Positionen, Historie, realisierter
Gewinn, Ereignisverlauf. ``_trade_to_dict`` ist die eine Stelle, an der eine
Datenbankzeile zur Trade-Dict-Form wird, die der Rest der Anwendung erwartet.

Geschrieben wird hier nichts — die Übergänge liegen in ``trades.py``.
"""

import json
from datetime import datetime, timedelta, timezone

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


def get_closed_trade_results_since(days: int = 45) -> list[dict]:
    """Rohdaten geschlossener Trades seit dem UTC-Tages-Cutoff (inklusive)."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=int(days))).isoformat()
    with db._database().transaction() as transaction:
        return transaction.all(
            "SELECT signal_json, pnl_pct FROM trades WHERE status = 'closed' "
            "AND pnl_pct IS NOT NULL AND trade_date >= :cutoff",
            {"cutoff": cutoff},
        )


# ── Trade-Tracking (ersetzt TradeTracker, jetzt pro user_id) ───────────────

def _trade_to_dict(row) -> dict:
    out = {
        "id":         row["id"] if "id" in row.keys() else None,
        "user_id":    row["user_id"] if "user_id" in row.keys() else None,
        "ticker":     row["ticker"],
        "direction":  row["direction"],
        "signal":     json.loads(row["signal_json"]),
        "message_id": row["message_id"],
        "status":     row["status"],
        "entry":      row["entry"],
        "exit":       row["exit"],
        "pnl_eur":    row["pnl_eur"],
        "pnl_pct":    row["pnl_pct"],
        "trade_date": row["trade_date"],
        "created_at": row["created_at"] if "created_at" in row.keys() else None,
    }
    keys = set(row.keys())
    for key in ("broker_order_id", "broker_status", "broker_filled_qty",
                "broker_filled_avg_price", "broker_updated_at", "high_water"):
        out[key] = row[key] if key in keys else None
    return out


def has_trade_today(user_id: int, ticker: str) -> bool:
    """True, wenn für diese Aktie heute bereits ein Signal/Trade existiert (egal welcher Status)."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT 1 AS present FROM trades WHERE user_id = :user_id "
            "AND trade_date = :trade_date AND ticker = :ticker LIMIT 1",
            {"user_id": user_id, "trade_date": db._today(), "ticker": ticker},
        )
    return row is not None


def has_open_position(user_id: int, ticker: str) -> bool:
    """Duplikat-Schutz fürs Senden: heute schon ein Datensatz ODER ein über Nacht offener
    (aktiver) Trade dieser Aktie (egal welches Datum)."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT 1 AS present FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND (trade_date = :trade_date OR status = 'active') LIMIT 1",
            {"user_id": user_id, "ticker": ticker, "trade_date": db._today()},
        )
    return row is not None


def get_active_trades(user_id: int) -> list[dict]:
    """Gibt ALLE aktiven Trades des Nutzers zurück (auch über Nacht gehaltene, datumsunabhängig)."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND status = 'active' "
            "ORDER BY trade_date ASC, id ASC", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_pending_trades(user_id: int) -> list[dict]:
    """Gibt alle heute noch ausstehenden (pending) Trades des Nutzers zurück."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND trade_date = :trade_date "
            "AND status = 'pending' ORDER BY id ASC",
            {"user_id": user_id, "trade_date": db._today()},
        )
    return [_trade_to_dict(r) for r in rows]


def get_broker_pending_trades(user_id: int) -> list[dict]:
    """Broker-Orders, die angenommen, aber noch nicht tatsächlich gefüllt wurden."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND status = 'broker_pending' "
            "ORDER BY trade_date ASC, id ASC", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_broker_closing_trades(user_id: int) -> list[dict]:
    """Broker-Schließungen, die angestoßen wurden, aber noch nicht final bestätigt sind."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND status = 'broker_closing' "
            "ORDER BY trade_date ASC, id ASC", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_trade(user_id: int, ticker: str) -> dict | None:
    """Relevantester Trade einer Aktie: aktiver (über Nacht gehaltener) zuerst, sonst der heutige.
    So funktioniert Verkaufen/Hebel auch bei datumsübergreifend offenen Trades."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT * FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND (status = 'active' OR trade_date = :trade_date) "
            "ORDER BY (status = 'active') DESC, trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker, "trade_date": db._today()},
        )
    return _trade_to_dict(row) if row else None


def get_trade_by_id(trade_id: int) -> dict | None:
    """Liefert einen Trade anhand seiner global eindeutigen ID (OMS-Signal-Bridge)."""
    with db._database().transaction() as transaction:
        row = transaction.one("SELECT * FROM trades WHERE id = :trade_id", {"trade_id": trade_id})
    return _trade_to_dict(row) if row else None


def get_history(user_id: int, days: int = 30) -> list[dict]:
    """Gibt die abgeschlossenen Trades der letzten N Tage zurück (neueste zuerst)."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=int(days))).isoformat()
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND status = 'closed' "
            "AND trade_date >= :cutoff ORDER BY trade_date DESC, id DESC",
            {"user_id": user_id, "cutoff": cutoff},
        )
    return [_trade_to_dict(r) for r in rows]


def get_closed_trades(user_id: int) -> list[dict]:
    """Alle abgeschlossenen Trades des Nutzers, älteste zuerst (für Equity-Kurve & Statistik)."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            """SELECT * FROM trades
               WHERE user_id = :user_id AND status = 'closed'
               ORDER BY trade_date ASC, id ASC""", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_realized_pnl_today(user_id: int) -> float:
    """Summe des heute REALISIERTEN P&L eines Nutzers (geschlossene Trades des UTC-Handelstags).

    Grundlage für das Tagesverlustlimit (RISK-004, ``pretrade_check`` Schritt 10): nur
    ``status = 'closed'``-Trades des heutigen ``trade_date`` (UTC, `today_utc()`) mit
    gesetztem ``pnl_eur`` zählen. Ohne solche Trades ist der realisierte Tages-P&L 0.0.
    Unrealisierte P&L offener Positionen fließen bewusst NICHT ein.
    """
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT COALESCE(SUM(pnl_eur), 0.0) AS realized FROM trades "
            "WHERE user_id = :user_id AND status = 'closed' AND trade_date = :trade_date "
            "AND pnl_eur IS NOT NULL",
            {"user_id": user_id, "trade_date": db.today_utc()},
        )
    return float(row["realized"]) if row and row["realized"] is not None else 0.0


def get_all_trades(user_id: int) -> list[dict]:
    """Alle Trades des Nutzers für Export/Analyse, älteste zuerst und statusübergreifend."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            """SELECT * FROM trades
               WHERE user_id = :user_id
               ORDER BY trade_date ASC, id ASC""", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_all_trades_between(user_id: int, date_from: str | None = None,
                           date_to: str | None = None) -> list[dict]:
    """Trades des Nutzers, optional auf `trade_date ∈ [date_from, date_to]` (inklusiv) gefiltert.
    Datumsformat 'YYYY-MM-DD'; None = keine Grenze."""
    sql = "SELECT * FROM trades WHERE user_id = :user_id"
    params = {"user_id": user_id}
    if date_from:
        sql += " AND trade_date >= :date_from"; params["date_from"] = date_from
    if date_to:
        sql += " AND trade_date <= :date_to"; params["date_to"] = date_to
    sql += " ORDER BY trade_date ASC, id ASC"
    with db._database().transaction() as transaction:
        rows = transaction.all(sql, params)
    return [_trade_to_dict(r) for r in rows]


def get_trade_events(trade_id: int) -> list[dict]:
    """Alle Status-Events eines Trades, chronologisch (für Dauer-Berechnung)."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trade_events WHERE trade_id = :trade_id ORDER BY ts ASC, id ASC",
            {"trade_id": trade_id},
        )
    return [dict(r) for r in rows]


def get_events_by_trade(user_id: int) -> dict[int, list[dict]]:
    """Alle Events des Nutzers, gruppiert nach trade_id (ein Query statt N — für den Export)."""
    out: dict[int, list[dict]] = {}
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trade_events WHERE user_id = :user_id ORDER BY ts ASC, id ASC",
            {"user_id": user_id},
        )
    for r in rows:
        out.setdefault(r["trade_id"], []).append(dict(r))
    return out


def get_trade_events_between(user_id: int, ts_from: str | None = None,
                             ts_to: str | None = None) -> list[dict]:
    """Status-Events des Nutzers im Zeitfenster [ts_from, ts_to] (inklusiv), chronologisch.
    `ts`-Format 'YYYY-MM-DD HH:MM:SS' (UTC); ein reines Datum filtert ab/bis Tagesgrenze."""
    sql = "SELECT * FROM trade_events WHERE user_id = :user_id"
    params = {"user_id": user_id}
    if ts_from:
        sql += " AND ts >= :ts_from"; params["ts_from"] = ts_from
    if ts_to:
        sql += " AND ts <= :ts_to"
        params["ts_to"] = ts_to + " 23:59:59" if len(ts_to) == 10 else ts_to
    sql += " ORDER BY ts ASC, id ASC"
    with db._database().transaction() as transaction:
        rows = transaction.all(sql, params)
    return [dict(r) for r in rows]
