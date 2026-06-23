"""
Status-Dauern aus dem Trade-Event-Log (Teil A des Tracking-Konzepts).

Reine, IO-freie Logik — gut testbar. Eingabe ist die chronologische Event-Liste eines Trades
(`db.get_trade_events`), jeweils ein Statuswechsel `from_status → to_status` mit Zeitstempel `ts`.

Aus der Abfolge lässt sich rekonstruieren, wie lange ein Trade in jedem Status war: nach einem
Event ist der Trade in dessen `to_status`, bis das nächste Event kommt (bzw. bis „jetzt", falls
der aktuelle Status nicht terminal ist).
"""

from datetime import datetime

# Endzustände: ab hier läuft keine Dauer mehr weiter.
TERMINAL = ("closed", "broker_failed", "rejected", "expired")

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")


def parse_ts(ts: str | None) -> datetime | None:
    """SQLite-Zeitstempel (UTC) → datetime; None bei leer/unparsebar."""
    if not ts:
        return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(ts, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def compute_durations(events: list[dict], now: datetime | None = None) -> dict:
    """Berechnet die Status-Dauern eines Trades aus seinen Events.

    Rückgabe (alle Sekunden, int oder None):
    - `by_status`: {status: akkumulierte Sekunden} über ALLE Besuche des Status.
    - `pending_sec`: Zeit in 'pending' (Signal offen bis Annahme/Ablauf).
    - `time_to_fill_sec`: Zeit in 'broker_pending' (Order angenommen bis Fill).
    - `hold_sec`: Zeit in 'active' (Haltedauer; läuft bei offenem Trade bis `now`).
    - `total_lifetime_sec`: erstes Event bis letztes Event (bzw. bis `now`, falls noch offen).
    - `is_open`: True, wenn der letzte Status nicht terminal ist.
    """
    empty = {"by_status": {}, "pending_sec": None, "time_to_fill_sec": None,
             "hold_sec": None, "total_lifetime_sec": None, "is_open": False}
    if not events:
        return empty
    now = now or datetime.utcnow()

    # (start_ts, status) je Segment aufbauen.
    points = []
    for e in events:
        t = parse_ts(e.get("ts"))
        if t is not None:
            points.append((t, e.get("to_status")))
    if not points:
        return empty

    last_status = points[-1][1]
    is_open = last_status not in TERMINAL

    by_status: dict[str, float] = {}
    for i, (start, status) in enumerate(points):
        if i + 1 < len(points):
            end = points[i + 1][0]
        else:
            end = now if is_open else start          # terminal → kein weiterer Lauf
        secs = max(0.0, (end - start).total_seconds())
        by_status[status] = by_status.get(status, 0.0) + secs

    first_ts = points[0][0]
    last_ts = now if is_open else points[-1][0]
    total = max(0.0, (last_ts - first_ts).total_seconds())

    return {
        "by_status": {k: int(round(v)) for k, v in by_status.items()},
        "pending_sec": int(round(by_status.get("pending", 0.0))) if "pending" in by_status else None,
        "time_to_fill_sec": int(round(by_status["broker_pending"])) if "broker_pending" in by_status else None,
        "hold_sec": int(round(by_status["active"])) if "active" in by_status else None,
        "total_lifetime_sec": int(round(total)),
        "is_open": is_open,
    }
