"""
Tests für das Alembic-Migrationstooling (PLAT-001, migrations/).

Führt die initiale Migration gegen eine temporäre SQLite-Datei aus (kein echter
Postgres-Server nötig) und prüft, dass alle 7 Tabellen aus docs/DB_SCHEMA_SQLITE.md
angelegt werden. Reine Schema-Prüfung — keine Datenübernahme (folgt als eigener
späterer Checklisten-Punkt).
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from stockbot.core import db_export
from stockbot.paths import PROJECT_ROOT

ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def _alembic_config(sqlite_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.attributes["sqlalchemy.url"] = f"sqlite:///{sqlite_path}"
    return cfg


def test_upgrade_head_creates_all_tables(tmp_path):
    sqlite_path = tmp_path / "migration_test.db"
    cfg = _alembic_config(sqlite_path)

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{sqlite_path}", future=True)
    tables = set(inspect(engine).get_table_names())
    for name in db_export.TABLES:
        assert name in tables


def test_downgrade_base_drops_all_tables(tmp_path):
    sqlite_path = tmp_path / "migration_test_down.db"
    cfg = _alembic_config(sqlite_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(f"sqlite:///{sqlite_path}", future=True)
    tables = set(inspect(engine).get_table_names())
    assert not (tables & set(db_export.TABLES))
