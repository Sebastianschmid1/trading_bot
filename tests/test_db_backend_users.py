"""Backend-parity contracts for the first PLAT-001 users read slice."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from stockbot import config
from stockbot.core import db, db_backend, db_pool


CHAT = 6_933_293_791
TOKEN = "backend-contract-token"


def _postgres_available() -> bool:
    try:
        engine = create_engine(config.POSTGRES_DSN, future=True, connect_args={"connect_timeout": 1})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(params=("sqlite", "postgres"))
def users_backend(request, tmp_path: Path, monkeypatch):
    backend = request.param
    monkeypatch.setattr(config, "DB_BACKEND", backend)
    if backend == "sqlite":
        db.DB_FILE = tmp_path / "users-contract.db"
        db.init_db()
        engine = create_engine(f"sqlite:///{db.DB_FILE}", future=True)
    else:
        if not _postgres_available():
            pytest.skip("kein echtes Postgres unter POSTGRES_DSN erreichbar")
        engine = create_engine(config.POSTGRES_DSN, future=True)

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
        connection.execute(text("DELETE FROM users WHERE user_id = :user_id"), {"user_id": CHAT})
        connection.execute(statement, {"user_id": CHAT, "key": key, "secret": secret, "token": TOKEN})
    db_pool.reset_engine()
    try:
        yield backend
    finally:
        db_pool.reset_engine()
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE user_id = :user_id"), {"user_id": CHAT})
        engine.dispose()


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
