"""PostgreSQL-Connection-Pool — Vorbereitung (PLAT-001, siehe docs/PLAN_CHECKLIST.md Phase 1).

Dieses Modul wird von KEINEM Live-Codepfad aufgerufen. `stockbot/core/db.py` (SQLite) bleibt
bis zum dokumentierten Cutover (docs/DB_SCHEMA_SQLITE.md, Migrationsstrategie Schritt 6/7)
die alleinige Quelle der Wahrheit für Bot/Web/Telegram. Zweck hier ist nur, den Connection
Pool und die Transaktionsgrenzen für die künftige Postgres-Anbindung technisch bereitzustellen
und testbar zu machen, bevor Domänenobjekte (nächster Checklisten-Punkt) darauf aufsetzen.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from stockbot import config

_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None


def build_engine_kwargs() -> dict:
    """Pool-/Timeout-Parameter aus der Config (`POSTGRES_POOL_*`) als Engine-Kwargs."""
    return {
        "pool_size": config.POSTGRES_POOL_SIZE,
        "max_overflow": config.POSTGRES_MAX_OVERFLOW,
        "pool_timeout": config.POSTGRES_POOL_TIMEOUT,
        "pool_recycle": config.POSTGRES_POOL_RECYCLE,
        "pool_pre_ping": True,
        "future": True,
    }


def get_engine() -> Engine:
    """Liefert die (lazy erzeugte, gecachte) Engine für `config.POSTGRES_DSN`.

    `create_engine` baut keine Verbindung auf — der Pool verbindet erst bei der ersten
    tatsächlichen Nutzung. Ein SQLite-DSN (z. B. in Tests) ignoriert Pool-Kwargs, die
    SQLAlchemy für den `NullPool`/`SingletonThreadPool` dieses Dialekts nicht kennt.
    """
    global _engine
    if _engine is None:
        dsn = config.POSTGRES_DSN
        kwargs = build_engine_kwargs()
        if dsn.startswith("sqlite"):
            kwargs = {"future": True}
        _engine = create_engine(dsn, **kwargs)
    return _engine


def reset_engine() -> None:
    """Verwirft die gecachte Engine/Session-Factory (nur für Tests, z. B. bei DSN-Wechsel)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


def _session_factory(engine: Engine | None = None) -> sessionmaker:
    global _SessionFactory
    if engine is not None:
        return sessionmaker(bind=engine, future=True)
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), future=True)
    return _SessionFactory


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Transaktionsgrenze (PLAT-001): commit bei Erfolg, rollback bei Exception.

    `engine` ist nur für Tests gedacht (z. B. eine In-Memory-SQLite-Engine statt der
    echten Postgres-Engine aus `get_engine()`).
    """
    session = _session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
