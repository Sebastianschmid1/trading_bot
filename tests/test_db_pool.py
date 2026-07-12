"""
Tests für den PostgreSQL-Connection-Pool (PLAT-001, stockbot/core/db_pool.py).

Läuft komplett offline gegen eine In-Memory-SQLite-Engine — es wird kein echter
Postgres-Server benötigt. Prüft nur die Pool-/Transaktions-Mechanik selbst; das
Modul wird von keinem Live-Codepfad genutzt (siehe Modul-Docstring).

`test_session_scope_*_on_real_postgres` laufen zusätzlich gegen ein echtes lokales
PostgreSQL (`config.POSTGRES_DSN`) und werden übersprungen, wenn keins erreichbar ist.
"""

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, Text, create_engine, select

from stockbot import config
from stockbot.core import db_pool


def _postgres_available() -> bool:
    try:
        engine = create_engine(config.POSTGRES_DSN, future=True)
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


def test_build_engine_kwargs_reflects_config(monkeypatch):
    monkeypatch.setattr(config, "POSTGRES_POOL_SIZE", 7)
    monkeypatch.setattr(config, "POSTGRES_MAX_OVERFLOW", 3)
    monkeypatch.setattr(config, "POSTGRES_POOL_TIMEOUT", 12)
    monkeypatch.setattr(config, "POSTGRES_POOL_RECYCLE", 999)

    kwargs = db_pool.build_engine_kwargs()

    assert kwargs["pool_size"] == 7
    assert kwargs["max_overflow"] == 3
    assert kwargs["pool_timeout"] == 12
    assert kwargs["pool_recycle"] == 999
    assert kwargs["pool_pre_ping"] is True


def test_get_engine_uses_configured_dsn(monkeypatch):
    monkeypatch.setattr(config, "POSTGRES_DSN", "sqlite:///:memory:")
    db_pool.reset_engine()
    try:
        engine = db_pool.get_engine()
        assert str(engine.url) == "sqlite:///:memory:"
        assert db_pool.get_engine() is engine  # gecacht, keine zweite Engine
    finally:
        db_pool.reset_engine()


def test_session_scope_commits_on_success():
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata = MetaData()
    demo = Table("demo", metadata, Column("id", Integer, primary_key=True), Column("val", Text))
    metadata.create_all(engine)

    with db_pool.session_scope(engine) as session:
        session.execute(demo.insert().values(id=1, val="a"))

    with engine.connect() as conn:
        rows = conn.execute(select(demo)).fetchall()
    assert rows == [(1, "a")]


def test_session_scope_rolls_back_on_exception():
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata = MetaData()
    demo = Table("demo", metadata, Column("id", Integer, primary_key=True), Column("val", Text))
    metadata.create_all(engine)

    class Boom(Exception):
        pass

    try:
        with db_pool.session_scope(engine) as session:
            session.execute(demo.insert().values(id=1, val="a"))
            raise Boom("kaputt")
    except Boom:
        pass

    with engine.connect() as conn:
        rows = conn.execute(select(demo)).fetchall()
    assert rows == []


@pytest.mark.skipif(
    not _postgres_available(), reason="kein lokaler Postgres unter POSTGRES_DSN erreichbar"
)
def test_session_scope_commits_on_success_on_real_postgres():
    engine = create_engine(config.POSTGRES_DSN, future=True)
    metadata = MetaData()
    demo = Table("pool_test_demo", metadata, Column("id", Integer, primary_key=True), Column("val", Text))
    metadata.drop_all(engine)
    metadata.create_all(engine)
    try:
        with db_pool.session_scope(engine) as session:
            session.execute(demo.insert().values(id=1, val="a"))

        with engine.connect() as conn:
            rows = conn.execute(select(demo)).fetchall()
        assert rows == [(1, "a")]
    finally:
        metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.skipif(
    not _postgres_available(), reason="kein lokaler Postgres unter POSTGRES_DSN erreichbar"
)
def test_session_scope_rolls_back_on_exception_on_real_postgres():
    engine = create_engine(config.POSTGRES_DSN, future=True)
    metadata = MetaData()
    demo = Table("pool_test_demo_rollback", metadata, Column("id", Integer, primary_key=True), Column("val", Text))
    metadata.drop_all(engine)
    metadata.create_all(engine)

    class Boom(Exception):
        pass

    try:
        try:
            with db_pool.session_scope(engine) as session:
                session.execute(demo.insert().values(id=1, val="a"))
                raise Boom("kaputt")
        except Boom:
            pass

        with engine.connect() as conn:
            rows = conn.execute(select(demo)).fetchall()
        assert rows == []
    finally:
        metadata.drop_all(engine)
        engine.dispose()
