"""
Tests für das Alembic-Migrationstooling (PLAT-001, migrations/).

Führt die initiale Migration gegen eine temporäre SQLite-Datei aus (kein echter
Postgres-Server nötig) und prüft, dass alle exportierten Tabellen aus docs/DB_SCHEMA_SQLITE.md
angelegt werden. Reine Schema-Prüfung — keine Datenübernahme (folgt als eigener
späterer Checklisten-Punkt).

`test_upgrade_head_creates_all_tables_on_real_postgres` läuft zusätzlich gegen ein
echtes lokales PostgreSQL (`config.POSTGRES_DSN`, Default passend zu `docker-compose.yml`)
— SQLite akzeptiert DDL-Abweichungen (Typen, Server-Defaults), die auf Postgres erst
auffallen. Wird übersprungen, wenn kein lokaler Postgres erreichbar ist (kein
Staging-/Produktions-Server — nur lokale Entwicklung, kein Deploy).
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import BigInteger, create_engine, inspect

from stockbot import config
from stockbot.core import db, db_export
from stockbot.paths import PROJECT_ROOT

ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

BIGINT_COLUMNS = {
    "users": {"user_id"},
    "trades": {"id", "user_id", "message_id"},
    "trade_ticks": {"id", "user_id"},
    "trades_archive": {"id", "user_id", "message_id"},
    "trade_ticks_archive": {"id", "user_id"},
    "sessions": {"user_id"},
    "notifications": {"id", "user_id"},
    "trade_events": {"id", "trade_id", "user_id"},
    "trade_intents": {"id", "user_id", "signal_id"},
    "orders": {"id", "trade_intent_id", "user_id"},
    "order_events": {"id", "order_id"},
}


def _postgres_available() -> bool:
    try:
        engine = create_engine(config.POSTGRES_DSN, future=True)
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


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


def test_upgrade_head_uses_bigint_for_external_and_growing_ids(tmp_path):
    sqlite_path = tmp_path / "migration_bigint_test.db"
    command.upgrade(_alembic_config(sqlite_path), "head")

    engine = create_engine(f"sqlite:///{sqlite_path}", future=True)
    try:
        schema = inspect(engine)
        for table_name, expected_columns in BIGINT_COLUMNS.items():
            columns = {column["name"]: column["type"] for column in schema.get_columns(table_name)}
            assert {
                name for name, column_type in columns.items()
                if isinstance(column_type, BigInteger)
            } == expected_columns
    finally:
        engine.dispose()


def test_target_schema_contains_runtime_columns_and_core_constraints(tmp_path):
    """SCHEMA_SQL bleibt der maschinenlesbare Vertrag für Namen/PK/NOT NULL."""
    runtime = sqlite3.connect(":memory:")
    runtime.executescript(db.SCHEMA_SQL)
    target_path = tmp_path / "migration_contract_test.db"
    command.upgrade(_alembic_config(target_path), "head")
    engine = create_engine(f"sqlite:///{target_path}", future=True)
    try:
        target = inspect(engine)
        for table_name in db_export.TABLES:
            runtime_columns = {
                row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
                for row in runtime.execute(f'PRAGMA table_info("{table_name}")')
            }
            target_columns = {column["name"]: column for column in target.get_columns(table_name)}
            assert runtime_columns.keys() <= target_columns.keys(), table_name
            for name, contract in runtime_columns.items():
                assert bool(target_columns[name]["primary_key"]) == contract["primary_key"], (
                    table_name, name, "primary_key"
                )
                if contract["not_null"]:
                    assert not target_columns[name]["nullable"], (table_name, name, "nullable")
    finally:
        runtime.close()
        engine.dispose()


def test_oms_constraints_and_index_exist_after_upgrade(tmp_path):
    target_path = tmp_path / "migration_oms_constraints.db"
    command.upgrade(_alembic_config(target_path), "head")
    engine = create_engine(f"sqlite:///{target_path}", future=True)
    try:
        schema = inspect(engine)
        order_uniques = {
            tuple(constraint["column_names"])
            for constraint in schema.get_unique_constraints("orders")
        }
        assert ("idempotency_key",) in order_uniques
        assert {fk["referred_table"] for fk in schema.get_foreign_keys("orders")} == {
            "trade_intents"
        }
        assert {fk["referred_table"] for fk in schema.get_foreign_keys("order_events")} == {
            "orders"
        }
        indexes = {
            index["name"]: index["column_names"]
            for index in schema.get_indexes("order_events")
        }
        assert indexes["idx_order_events_order"] == ["order_id", "id"]
    finally:
        engine.dispose()


def test_downgrade_base_drops_all_tables(tmp_path):
    sqlite_path = tmp_path / "migration_test_down.db"
    cfg = _alembic_config(sqlite_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(f"sqlite:///{sqlite_path}", future=True)
    tables = set(inspect(engine).get_table_names())
    assert not (tables & set(db_export.TABLES))


@pytest.mark.skipif(
    not _postgres_available(), reason="kein lokaler Postgres unter POSTGRES_DSN erreichbar"
)
def test_upgrade_head_creates_all_tables_on_real_postgres():
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.attributes["sqlalchemy.url"] = config.POSTGRES_DSN

    command.downgrade(cfg, "base")  # sauberer Start, falls ein Vorlauf Tabellen hinterließ
    try:
        command.upgrade(cfg, "head")
        engine = create_engine(config.POSTGRES_DSN, future=True)
        try:
            tables = set(inspect(engine).get_table_names())
            for name in db_export.TABLES:
                assert name in tables
        finally:
            engine.dispose()
    finally:
        command.downgrade(cfg, "base")
