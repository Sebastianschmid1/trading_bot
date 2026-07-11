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
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.engine import Engine

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
}


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
    return inserted


def compare_snapshot_to_engine(snapshot: dict[str, Any], engine: Engine) -> dict[str, dict]:
    """Vergleicht Zeilenzahlen + Summen (Migrationsstrategie Schritt 5).

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
                expected_sum = sum(float(r[col]) for r in expected_rows if r.get(col) is not None)
                actual_sum = conn.execute(select(func.sum(table.c[col]))).scalar_one() or 0.0
                if abs(expected_sum - float(actual_sum)) > 1e-9:
                    table_diff[f"sum_{col}"] = {"expected": expected_sum, "actual": float(actual_sum)}
            if table_diff:
                mismatches[name] = table_diff
    return mismatches
