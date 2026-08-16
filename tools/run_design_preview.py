#!/usr/bin/env python3
"""Startet die Web-App gegen die Design-Seed-DB statt gegen `data/bot.db`.

Gegenstück zu `tools/seed_design_data.py`: das Seed-Skript baut die Demo-Datenbank, dieses
Skript serviert sie. Beides zusammen ist die Voraussetzung für die visuelle Abnahme durch den
`design-lead`-Agenten (siehe `.claude/agents/design-lead.md`) — ohne Daten zeigt die App neun
leere Seiten.

`db.DB_FILE` ist ein Modulattribut ohne Umgebungsvariable (`core/db.py`), deshalb wird es hier
**vor dem ersten DB-Zugriff** umgebogen. Die echte Entwickler-DB `data/bot.db` wird dabei nie
angefasst.

Nutzung:
    ENCRYPTION_KEY=<Fernet-Key> python tools/run_design_preview.py
    ENCRYPTION_KEY=<Fernet-Key> python tools/run_design_preview.py --db-path data/eigene.db --port 8010

Nur für die lokale Entwicklung — läuft ausschließlich gegen SQLite und weigert sich, wenn
`DB_BACKEND` etwas anderes sagt (dieselbe Auflage wie im Seed-Skript).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stockbot import config

if config.DB_BACKEND != "sqlite":
    raise SystemExit(
        f"ABBRUCH: DB_BACKEND='{config.DB_BACKEND}' — die Design-Vorschau läuft ausschließlich "
        "gegen eine lokale SQLite-Datei. Setze DB_BACKEND=sqlite (oder lass die Variable unset)."
    )

from stockbot.core import db  # noqa: E402  (erst nach dem Backend-Guard importieren)
from stockbot.paths import PROJECT_ROOT  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "data" / "design_seed.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB,
                        help=f"SQLite-Datei, die serviert wird (Default: {DEFAULT_DB})")
    parser.add_argument("--port", type=int, default=config.DASHBOARD_PORT,
                        help=f"Port (Default: {config.DASHBOARD_PORT})")
    parser.add_argument("--host", default="127.0.0.1", help="Bind-Adresse (Default: 127.0.0.1)")
    args = parser.parse_args()

    target = args.db_path.resolve()
    if not target.exists():
        print(f"FEHLER: {target} existiert nicht — erst `python tools/seed_design_data.py` laufen lassen.",
              file=sys.stderr)
        return 1

    # Der eigentliche Kniff: Pfad umbiegen, BEVOR irgendeine Verbindung geöffnet wurde.
    db.DB_FILE = target
    print(f"→ Design-Vorschau auf {target}")
    print(f"→ http://{args.host}:{args.port}/  (Token-Link: siehe Ausgabe des Seed-Skripts)")

    import uvicorn  # spät importiert, damit der Guard oben zuerst greift
    from stockbot.web.dashboard import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
