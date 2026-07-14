"""Additiver Read-Seam für die schrittweise SQLite→PostgreSQL-Umstellung."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from typing import Any, Callable, Iterator, Mapping, Protocol

from sqlalchemy import text

from stockbot.core import db_pool


class DbTransaction(Protocol):
    def one(self, statement: str, params: Mapping[str, Any]) -> dict | None: ...

    def all(self, statement: str, params: Mapping[str, Any] | None = None) -> list[dict]: ...

    def execute(self, statement: str, params: Mapping[str, Any] | None = None) -> int: ...

    def insert_id(self, statement: str, params: Mapping[str, Any]) -> int: ...


class Database(Protocol):
    def transaction(self) -> AbstractContextManager[DbTransaction]: ...


def _normalise_value(value: Any) -> Any:
    if isinstance(value, (memoryview, bytearray)):
        return bytes(value)
    return value


def _normalise_row(row: Mapping[str, Any]) -> dict:
    return {key: _normalise_value(value) for key, value in dict(row).items()}


class _SqliteTransaction:
    def __init__(self, connection: Any):
        self._connection = connection

    def one(self, statement: str, params: Mapping[str, Any]) -> dict | None:
        row = self._connection.execute(statement, params).fetchone()
        return _normalise_row(row) if row is not None else None

    def all(self, statement: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        rows = self._connection.execute(statement, params or {}).fetchall()
        return [_normalise_row(row) for row in rows]

    def execute(self, statement: str, params: Mapping[str, Any] | None = None) -> int:
        return self._connection.execute(statement, params or {}).rowcount

    def insert_id(self, statement: str, params: Mapping[str, Any]) -> int:
        return int(self._connection.execute(statement, params).lastrowid)


class SqliteDatabase:
    def __init__(self, connection_factory: Callable[[], AbstractContextManager[Any]]):
        self._connection_factory = connection_factory

    @contextmanager
    def transaction(self) -> Iterator[DbTransaction]:
        with self._connection_factory() as connection:
            yield _SqliteTransaction(connection)


class _PostgresTransaction:
    def __init__(self, connection: Any):
        self._connection = connection

    def one(self, statement: str, params: Mapping[str, Any]) -> dict | None:
        row = self._connection.execute(text(statement), params).mappings().first()
        return _normalise_row(row) if row is not None else None

    def all(self, statement: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        rows = self._connection.execute(text(statement), params or {}).mappings().all()
        return [_normalise_row(row) for row in rows]

    def execute(self, statement: str, params: Mapping[str, Any] | None = None) -> int:
        return self._connection.execute(text(statement), params or {}).rowcount

    def insert_id(self, statement: str, params: Mapping[str, Any]) -> int:
        return int(self._connection.execute(text(f"{statement} RETURNING id"), params).scalar_one())


class PostgresDatabase:
    def __init__(self, engine: Any | None = None):
        self._engine = engine

    @contextmanager
    def transaction(self) -> Iterator[DbTransaction]:
        engine = self._engine or db_pool.get_engine()
        with engine.begin() as connection:
            yield _PostgresTransaction(connection)


def get_database(
    backend: str,
    sqlite_connection_factory: Callable | None = None,
    postgres_engine: Any | None = None,
) -> Database:
    if backend == "sqlite":
        if sqlite_connection_factory is None:
            raise ValueError("sqlite_connection_factory fehlt")
        return SqliteDatabase(sqlite_connection_factory)
    if backend == "postgres":
        return PostgresDatabase(postgres_engine)
    raise ValueError(f"Unbekanntes DB-Backend: {backend}")
