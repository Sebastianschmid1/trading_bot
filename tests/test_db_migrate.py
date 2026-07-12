"""
Tests für die Datenübernahme SQLite → Zielschema (PLAT-001, stockbot/core/db_migrate.py).

Baut eine Testmigration auf einer KOPIE nach: Quelle ist eine temporäre SQLite-DB mit
Beispieldaten (db.py), Ziel ist eine zweite, per Alembic angelegte temporäre SQLite-DB
(steht stellvertretend für Postgres — beide laufen über dieselbe SQLAlchemy-Core-API).
Prüft anschließend Zeilen/Summen-Übereinstimmung (Migrationsstrategie Schritt 4/5).

`test_migrate_snapshot_row_and_sum_counts_match_on_real_postgres` läuft zusätzlich gegen ein
echtes lokales PostgreSQL (`config.POSTGRES_DSN`) als Ziel-Engine — insbesondere die
BLOB-Spalten (`broker_api_key`/`_secret`) verhalten sich auf Postgres (`BYTEA`) anders als
auf SQLite. Wird übersprungen, wenn kein lokaler Postgres erreichbar ist.
"""

import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from stockbot import config
from stockbot.core import db, db_export, db_migrate
from stockbot.paths import PROJECT_ROOT

ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
CHAT_A, CHAT_B = 4001, 4002


def _alembic_config(sqlite_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.attributes["sqlalchemy.url"] = f"sqlite:///{sqlite_path}"
    return cfg


def _postgres_available() -> bool:
    try:
        engine = create_engine(config.POSTGRES_DSN, future=True)
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


def _seed_source_db(tmp_path: Path) -> Path:
    db.DB_FILE = tmp_path / "source.db"
    db.init_db()
    db.get_or_create_user(CHAT_A, "alice")
    db.get_or_create_user(CHAT_B, "bob")
    db.save_profile(CHAT_A, trade_size_eur=100.0, broker_platform="alpaca",
                     broker_api_key="key-a", broker_api_secret="secret-a")
    db.add_pending(CHAT_A, {"ticker": "AAPL", "direction": "long"}, message_id=1)
    db.add_pending(CHAT_B, {"ticker": "MSFT", "direction": "long"}, message_id=2)
    return db.DB_FILE


def _new_target_engine(tmp_path: Path, name: str):
    target_path = tmp_path / name
    command.upgrade(_alembic_config(target_path), "head")
    return create_engine(f"sqlite:///{target_path}", future=True)


def test_migrate_snapshot_row_and_sum_counts_match(tmp_path):
    _seed_source_db(tmp_path)
    snapshot = db_export.export_snapshot(tmp_path / "exports", stamp="20260711T000003Z")
    data = db_migrate.load_snapshot(snapshot)

    target_engine = _new_target_engine(tmp_path, "target.db")
    inserted = db_migrate.migrate_snapshot_to_engine(data, target_engine)

    assert inserted["users"] == 2
    assert inserted["trades"] == 2
    mismatches = db_migrate.compare_snapshot_to_engine(data, target_engine)
    assert mismatches == {}


def test_migrate_detects_missing_row(tmp_path):
    _seed_source_db(tmp_path)
    snapshot = db_export.export_snapshot(tmp_path / "exports", stamp="20260711T000004Z")
    data = db_migrate.load_snapshot(snapshot)

    target_engine = _new_target_engine(tmp_path, "target_partial.db")
    partial = dict(data)
    partial["tables"] = dict(data["tables"])
    partial["tables"]["trades"] = data["tables"]["trades"][:1]     # eine Zeile "verlieren"
    db_migrate.migrate_snapshot_to_engine(partial, target_engine)

    mismatches = db_migrate.compare_snapshot_to_engine(data, target_engine)
    assert "trades" in mismatches
    assert mismatches["trades"]["row_count"]["expected"] == 2
    assert mismatches["trades"]["row_count"]["actual"] == 1


def test_migrate_keeps_broker_credentials_decodable(tmp_path):
    _seed_source_db(tmp_path)
    snapshot = db_export.export_snapshot(tmp_path / "exports", stamp="20260711T000005Z")
    data = db_migrate.load_snapshot(snapshot)

    target_engine = _new_target_engine(tmp_path, "target_creds.db")
    db_migrate.migrate_snapshot_to_engine(data, target_engine)

    with target_engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT broker_api_key FROM users WHERE user_id=?", (CHAT_A,)
        ).fetchone()
    assert isinstance(row[0], (bytes, bytearray))
    assert db.decrypt(bytes(row[0])) == "key-a"


@pytest.mark.skipif(
    not _postgres_available(), reason="kein lokaler Postgres unter POSTGRES_DSN erreichbar"
)
def test_migrate_snapshot_row_and_sum_counts_match_on_real_postgres(tmp_path):
    _seed_source_db(tmp_path)
    snapshot = db_export.export_snapshot(tmp_path / "exports", stamp="20260712T000001Z")
    data = db_migrate.load_snapshot(snapshot)

    pg_cfg = Config(str(ALEMBIC_INI))
    pg_cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    pg_cfg.attributes["sqlalchemy.url"] = config.POSTGRES_DSN
    command.downgrade(pg_cfg, "base")  # sauberer Start, falls ein Vorlauf Tabellen hinterließ
    command.upgrade(pg_cfg, "head")
    target_engine = create_engine(config.POSTGRES_DSN, future=True)
    try:
        inserted = db_migrate.migrate_snapshot_to_engine(data, target_engine)

        assert inserted["users"] == 2
        assert inserted["trades"] == 2
        mismatches = db_migrate.compare_snapshot_to_engine(data, target_engine)
        assert mismatches == {}

        with target_engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT broker_api_key FROM users WHERE user_id=%s", (CHAT_A,)
            ).fetchone()
        # psycopg2 liefert BYTEA als memoryview zurück, nicht als bytes (anders als SQLite).
        assert isinstance(row[0], (bytes, bytearray, memoryview))
        assert db.decrypt(bytes(row[0])) == "key-a"
    finally:
        target_engine.dispose()
        command.downgrade(pg_cfg, "base")
