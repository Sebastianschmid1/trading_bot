"""Datenübernahme SQLite → Zielschema (PLAT-001, Migrationsstrategie Schritt 3–5,
siehe docs/DB_SCHEMA_SQLITE.md). Nimmt einen bereits gezogenen Snapshot
(`stockbot/core/db_export.py`) und schreibt ihn in eine Zielengine, deren Schema
bereits per Alembic angelegt wurde (`migrations/`). Anschließend werden Zeilenzahlen
und Summen zwischen Snapshot und Ziel verglichen.

Läuft ausschließlich gegen eine KOPIE (Snapshot-Datei + separate Ziel-Engine) — die
laufende Paper-DB (`stockbot/core/db.py`) wird dabei nie angefasst. Der eigentliche
Cutover (Paper-Laufzeit liest/schreibt Postgres) ist ein eigener, späterer Schritt,
der ein echtes Staging-Postgres voraussetzt (Migrationsstrategie Schritt 6/7).
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Any

from sqlalchemy import Float, MetaData, Table, func, select, text
from sqlalchemy.engine import Connection, Engine

from stockbot.core.db_export import TABLES

# Spalten, die im Snapshot Base64-kodierte BLOBs sind (siehe db_export._json_default)
# und beim Insert wieder in echte `bytes` zurückverwandelt werden müssen.
_BLOB_COLUMNS = {"broker_api_key", "broker_api_secret"}

# Numerische Spalten je Tabelle, deren Summe zusätzlich zur Zeilenzahl verglichen wird
# (Migrationsstrategie Schritt 5: "Zeilen/Summen vergleichen").
_SUM_COLUMNS = {
    "users": ("trade_size_eur",),
    "trades": ("pnl_eur", "pnl_pct"),
    "trade_ticks": ("price", "strength"),
    # qty und notional sind alternative Ordergroessen (je nach Ordertyp ist eine
    # davon NULL). Beide Summen erkennen dennoch verlorene oder veraenderte Werte.
    "orders": ("qty", "notional"),
}

_AUTOINCREMENT_IDS = (
    "trades",
    "trade_ticks",
    "trades_archive",
    "trade_ticks_archive",
    "notifications",
    "trade_events",
    "trade_intents",
    "orders",
    "order_events",
)

_FLOAT_SUM_REL_TOL = 1e-9


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _decode_row(table_name: str, row: dict[str, Any]) -> dict[str, Any]:
    if table_name != "users":
        return row
    out = dict(row)
    for col in _BLOB_COLUMNS:
        if out.get(col) is not None:
            out[col] = base64.b64decode(out[col])
    return out


def migrate_snapshot_to_engine(snapshot: dict[str, Any], engine: Engine) -> dict[str, int]:
    """Schreibt alle Tabellen eines Snapshots in `engine` (Schema muss bereits existieren).

    Reihenfolge = `TABLES` (users vor trades/sessions wegen Fremdschlüssel). Gibt je
    Tabelle die Anzahl eingefügter Zeilen zurück.
    """
    metadata = MetaData()
    inserted: dict[str, int] = {}
    with engine.begin() as conn:
        for name in TABLES:
            rows = snapshot["tables"].get(name, [])
            inserted[name] = 0
            if not rows:
                continue
            table = Table(name, metadata, autoload_with=engine)
            conn.execute(table.insert(), [_decode_row(name, r) for r in rows])
            inserted[name] = len(rows)
        _sync_sequences(conn)
    return inserted


def sync_sequences(engine: Engine) -> None:
    """Setzt PostgreSQL-ID-Sequences nach Inserts mit expliziten IDs fort.

    SQLite aktualisiert seine interne Sequence bereits bei expliziten INTEGER-PKs;
    dort (und auf anderen Dialekten) ist dieser Helfer bewusst ein No-op.
    """
    with engine.begin() as conn:
        _sync_sequences(conn)


def _sync_sequences(conn: Connection) -> None:
    if conn.dialect.name != "postgresql":
        return
    metadata = MetaData()
    for name in _AUTOINCREMENT_IDS:
        table = Table(name, metadata, autoload_with=conn)
        next_id = conn.execute(
            select(func.coalesce(func.max(table.c.id), 0) + 1)
        ).scalar_one()
        sequence = conn.execute(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": name},
        ).scalar_one()
        if sequence is not None:
            conn.execute(
                text("SELECT setval(CAST(:sequence AS regclass), :next_id, false)"),
                {"sequence": sequence, "next_id": next_id},
            )


def compare_snapshot_to_engine(snapshot: dict[str, Any], engine: Engine) -> dict[str, dict]:
    """Vergleicht Zeilenzahlen + Summen (Migrationsstrategie Schritt 5).

    Zeilenzahlen und Summen exakter Zahlentypen werden exakt verglichen. Bei Float-Summen
    gilt eine relative Toleranz von 1e-9: Das ist groß genug für Akkumulationsrauschen durch
    unterschiedliche Summierreihenfolgen in SQLite und PostgreSQL, aber streng genug, um
    echte Datenfehler zu erkennen. Schon eine fehlende/duplizierte Zeile oder ein
    verfälschter Wert verändert typische Summen um viele Größenordnungen mehr.

    Gibt für jede abweichende Tabelle `{"row_count": {"expected": .., "actual": ..}, ...}`
    zurück; ein leeres Dict bedeutet: Snapshot und Ziel stimmen vollständig überein.
    """
    metadata = MetaData()
    mismatches: dict[str, dict] = {}
    with engine.connect() as conn:
        for name in TABLES:
            expected_rows = snapshot["tables"].get(name, [])
            expected_count = len(expected_rows)
            table = Table(name, metadata, autoload_with=engine)
            actual_count = conn.execute(select(func.count()).select_from(table)).scalar_one()
            table_diff: dict[str, Any] = {}
            if expected_count != actual_count:
                table_diff["row_count"] = {"expected": expected_count, "actual": actual_count}
            for col in _SUM_COLUMNS.get(name, ()):
                expected_sum = sum(r[col] for r in expected_rows if r.get(col) is not None)
                actual_sum = conn.execute(select(func.sum(table.c[col]))).scalar_one() or 0
                if isinstance(table.c[col].type, Float):
                    expected_sum = float(expected_sum)
                    actual_sum = float(actual_sum)
                    sums_match = math.isclose(
                        expected_sum, actual_sum, rel_tol=_FLOAT_SUM_REL_TOL
                    )
                else:
                    sums_match = expected_sum == actual_sum
                if not sums_match:
                    table_diff[f"sum_{col}"] = {
                        "expected": expected_sum,
                        "actual": actual_sum,
                    }
            if table_diff:
                mismatches[name] = table_diff
    return mismatches
