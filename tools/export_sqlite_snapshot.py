#!/usr/bin/env python3
"""CLI: Read-only-Snapshot der SQLite-DB schreiben (PLAT-001, vor PostgreSQL-Migration).

Schreibt data/sqlite_exports/sqlite_snapshot_<UTC-Zeitstempel>.json — alle Tabellen als
Zeilenlisten plus Zeilenanzahlen je Tabelle. Öffnet die DB read-only, verändert also
nichts. Details: docs/DB_SCHEMA_SQLITE.md.

Beispiel:
    python tools/export_sqlite_snapshot.py
"""
from __future__ import annotations

from stockbot.core.db_export import export_snapshot


def main() -> None:
    path = export_snapshot()
    print(f"Snapshot geschrieben: {path}")


if __name__ == "__main__":
    main()
