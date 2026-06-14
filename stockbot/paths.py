"""Zentrale Pfade des Projekts — unabhängig vom Arbeitsverzeichnis.

Alle Laufzeitdaten (SQLite-DB, Caches) liegen unter DATA_DIR, die .env im Projekt-Root.
Dieses Modul importiert bewusst nichts Projektinternes (kein Import-Zyklus).
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]   # = Repo-Root (enthält stockbot/, data/, .env)
DATA_DIR = PROJECT_ROOT / "data"
ENV_FILE = PROJECT_ROOT / ".env"
REPORTS_DIR = DATA_DIR / "reports"                   # versionierte Backtest-Reports (JSON, für die Website)
