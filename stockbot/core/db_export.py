"""Read-only Snapshot-Export der SQLite-DB (PLAT-001, Vorbereitung PostgreSQL-Migration).

Schreibt einen vollständigen, tabellenweisen JSON-Export plus Zeilenanzahlen je Tabelle
(Manifest). Öffnet die DB read-only (`mode=ro`), damit der Export selbst unter keinen
Umständen Daten verändert oder Locks hält. Dient als eingefrorener Ausgangsstand, gegen
den nach der eigentlichen PostgreSQL-Migration Zeilen/Summen verglichen werden
(docs/DB_SCHEMA_SQLITE.md, Migrationsstrategie Schritt 2).

Verschlüsselte Spalten (`broker_api_key`/`broker_api_secret`) bleiben verschlüsselt
(BLOB → Base64) im Export — es wird nichts entschlüsselt.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from stockbot.core import db
from stockbot.paths import DATA_DIR

EXPORT_DIR = DATA_DIR / "sqlite_exports"

# Reihenfolge wie in db.SCHEMA_SQL. Tabellen, die in einer älteren DB noch fehlen,
# werden beim Export übersprungen (siehe export_snapshot).
TABLES = (
    "users",
    "trades",
    "trade_ticks",
    "sessions",
    "notifications",
    "strategy_configs",
    "trade_events",
)


def _json_default(obj):
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(bytes(obj)).decode("ascii")
    return str(obj)


def _connect_readonly(db_file: Path) -> sqlite3.Connection:
    """Öffnet `db_file` strikt lesend (SQLite-URI `mode=ro`) — Schreibversuche schlagen fehl."""
    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def export_snapshot(dest_dir: Path | None = None, *, stamp: str | None = None) -> Path:
    """Exportiert alle Tabellen der aktuellen `db.DB_FILE` als ein JSON-Snapshot.

    Gibt den Pfad der geschriebenen Datei zurück. `stamp` überschreibt den Zeitstempel
    im Dateinamen (für reproduzierbare Tests); Default ist die aktuelle UTC-Zeit.
    """
    dest_dir = dest_dir or EXPORT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    tables: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    conn = _connect_readonly(db.DB_FILE)
    try:
        for name in TABLES:
            try:
                rows = conn.execute(f"SELECT * FROM {name}").fetchall()
            except sqlite3.OperationalError:
                continue  # Tabelle existiert (noch) nicht in dieser DB — überspringen
            tables[name] = [dict(r) for r in rows]
            counts[name] = len(rows)
    finally:
        conn.close()

    snapshot = {
        "exported_at": stamp,
        "source_db": str(db.DB_FILE),
        "row_counts": counts,
        "tables": tables,
    }
    out_path = dest_dir / f"sqlite_snapshot_{stamp}.json"
    out_path.write_text(
        json.dumps(snapshot, default=_json_default, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path
