"""Backend-parity contracts for the first PLAT-001 users read slice."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from stockbot import config
from stockbot.core import db, db_backend, db_pool
from stockbot.paths import PROJECT_ROOT


CHAT = 6_933_293_791
TOKEN = "backend-contract-token"
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def _contract_database_urls():
    configured_url = make_url(config.POSTGRES_DSN)
    database = configured_url.database or "postgres"
    return configured_url.set(database=database), configured_url.set(
        database=f"{database}_contract_test"
    )


def _drop_contract_database(maintenance_engine, database: str) -> None:
    quoted_database = maintenance_engine.dialect.identifier_preparer.quote(database)
    with maintenance_engine.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_database}"))


def _provision_contract_database():
    maintenance_url, test_url = _contract_database_urls()
    connect_args = {"connect_timeout": 1}
    maintenance_engine = create_engine(
        maintenance_url, future=True, isolation_level="AUTOCOMMIT", connect_args=connect_args
    )
    database = test_url.database
    quoted_database = maintenance_engine.dialect.identifier_preparer.quote(database)
    try:
        with maintenance_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :database"),
                {"database": database},
            ).scalar()
            if exists:
                connection.execute(text(f"DROP DATABASE {quoted_database}"))
            connection.execute(text(f"CREATE DATABASE {quoted_database}"))
    except Exception as exc:
        maintenance_engine.dispose()
        pytest.skip(
            "Postgres-Contract-DB kann nicht angelegt werden "
            f"(Wartungsverbindung/CREATE DATABASE fehlt): {exc}"
        )

    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    alembic_cfg.attributes["sqlalchemy.url"] = test_url.render_as_string(hide_password=False)
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception:
        _drop_contract_database(maintenance_engine, database)
        maintenance_engine.dispose()
        raise
    return maintenance_engine, test_url


@pytest.fixture(params=("sqlite", "postgres"))
def users_backend(request, tmp_path: Path, monkeypatch):
    backend = request.param
    monkeypatch.setattr(config, "DB_BACKEND", backend)
    if backend == "sqlite":
        db.DB_FILE = tmp_path / "users-contract.db"
        db.init_db()
        engine = create_engine(f"sqlite:///{db.DB_FILE}", future=True)
        maintenance_engine = None
    else:
        maintenance_engine, test_url = _provision_contract_database()
        engine = create_engine(test_url, future=True)
        original_get_database = db_backend.get_database

        def get_test_database(selected_backend, sqlite_connection_factory=None, **kwargs):
            return original_get_database(
                selected_backend,
                sqlite_connection_factory,
                postgres_engine=engine if selected_backend == "postgres" else None,
                **kwargs,
            )

        monkeypatch.setattr(db_backend, "get_database", get_test_database)

    try:
        key = db.encrypt("contract-key")
        secret = db.encrypt("contract-secret")
        statement = text(
            """INSERT INTO users
               (user_id, username, trade_size_eur, broker_platform, broker_api_key,
                broker_api_secret, onboarding_state, is_active, dashboard_token,
                market_region, top_n_signals, sl_tp_mode, leverage, auto_accept,
                auto_universe, strategy, llm_rank, eod_close, broker_exec, signal_window,
                watchlist, notify_channel, asset_pref)
               VALUES
               (:user_id, 'contract-user', 125.5, 'alpaca', :key, :secret, 'complete', 1,
                :token, 'sp500,emerging', 7, 'normal', 2.0, 0, 1, 'standard', 1, 0,
                1, 0, 'AAPL,MSFT', 'both', 'stocks')"""
        )
        with engine.begin() as connection:
            connection.execute(
                statement, {"user_id": CHAT, "key": key, "secret": secret, "token": TOKEN}
            )
        db_pool.reset_engine()
        yield backend
    finally:
        db_pool.reset_engine()
        engine.dispose()
        if maintenance_engine is not None:
            _drop_contract_database(maintenance_engine, test_url.database)
            maintenance_engine.dispose()


def test_users_read_contract(users_backend):
    user = db.get_user(CHAT)
    assert user == {
        "user_id": CHAT, "username": "contract-user", "trade_size_eur": 125.5,
        "broker_platform": "alpaca", "onboarding_state": "complete", "is_active": True,
        "market_region": "sp500", "market_regions": ["sp500", "emerging"],
        "top_n_signals": 7, "sl_tp_mode": "normal", "leverage": 2.0,
        "auto_accept": False, "auto_universe": True, "strategy": "standard",
        "strategies": ["standard"], "llm_rank": True, "eod_close": False,
        "broker_exec": True, "signal_window": False, "watchlist": ["AAPL", "MSFT"],
        "notify_channel": "both", "asset_pref": "stocks",
    }
    assert user["user_id"] > 2**31
    assert all(type(user[key]) is bool for key in (
        "is_active", "auto_accept", "auto_universe", "llm_rank", "eod_close",
        "broker_exec", "signal_window",
    ))
    assert db.list_active_users() == [user]
    assert db.get_user_by_token(TOKEN) == user
    assert db.has_alpaca_credentials(CHAT) is True
    assert db.get_decrypted_credentials(CHAT) == ("contract-key", "contract-secret")
    assert db.get_user(CHAT + 1) is None


def test_empty_token_never_queries_backend(users_backend):
    assert db.get_user_by_token("") is None


def test_bytea_memoryview_is_normalised_to_bytes():
    value = b"fernet-ciphertext"
    assert db_backend._normalise_row({"broker_api_key": memoryview(value)}) == {
        "broker_api_key": value
    }
