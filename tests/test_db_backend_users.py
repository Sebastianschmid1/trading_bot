"""Backend-parity contracts for the first PLAT-001 users read slice."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from stockbot import config
from stockbot.core import db, db_backend, db_pool
from stockbot.optimize import lab
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


def _seed_trade_contract_rows():
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    rows = [
        {"id": 101, "trade_date": yesterday.isoformat(), "ticker": "AAPL",
         "status": "active", "entry": 10.5, "exit": None, "pnl_eur": None,
         "pnl_pct": None, "broker_status": None},
        {"id": 102, "trade_date": today.isoformat(), "ticker": "AAPL",
         "status": "closed", "entry": 11.0, "exit": 12.25, "pnl_eur": 2.5,
         "pnl_pct": 11.36, "broker_status": "filled"},
        {"id": 103, "trade_date": today.isoformat(), "ticker": "MSFT",
         "status": "pending", "entry": None, "exit": None, "pnl_eur": None,
         "pnl_pct": None, "broker_status": None},
    ]
    with db._database().transaction() as transaction:
        for row in rows:
            transaction.execute(
                """INSERT INTO trades
                   (id, user_id, trade_date, ticker, direction, signal_json, message_id,
                    status, entry, exit, pnl_eur, pnl_pct, broker_status, created_at)
                   VALUES (:id, :user_id, :trade_date, :ticker, 'long', :signal_json, 7,
                           :status, :entry, :exit, :pnl_eur, :pnl_pct, :broker_status,
                           :created_at)""",
                {**row, "user_id": CHAT, "signal_json": json.dumps({"ticker": row["ticker"]}),
                 "created_at": f'{row["trade_date"]} 08:00:00'},
            )
        for event in (
            {"id": 201, "trade_id": 101, "ticker": "AAPL", "trade_date": yesterday.isoformat(),
             "from_status": None, "to_status": "active", "ts": f"{yesterday} 09:00:00"},
            {"id": 202, "trade_id": 102, "ticker": "AAPL", "trade_date": today.isoformat(),
             "from_status": "active", "to_status": "closed", "ts": f"{today} 16:00:00"},
        ):
            transaction.execute(
                """INSERT INTO trade_events
                   (id, trade_id, user_id, ticker, trade_date, from_status, to_status, ts)
                   VALUES (:id, :trade_id, :user_id, :ticker, :trade_date, :from_status,
                           :to_status, :ts)""", {**event, "user_id": CHAT},
            )
    return yesterday.isoformat(), today.isoformat()


def test_trade_read_mapping_order_and_day_contract(users_backend):
    yesterday, today = _seed_trade_contract_rows()

    assert db.has_trade_today(CHAT, "AAPL") is True
    assert db.has_trade_today(CHAT, "NONE") is False
    assert db.has_open_position(CHAT, "AAPL") is True
    assert [row["id"] for row in db.get_all_trades(CHAT)] == [101, 102, 103]
    assert [row["id"] for row in db.get_pending_trades(CHAT)] == [103]
    assert [row["id"] for row in db.get_active_trades(CHAT)] == [101]
    assert db.get_trade(CHAT, "AAPL")["id"] == 101  # active outranks today's closed row
    assert db.get_trade_by_id(102)["signal"] == {"ticker": "AAPL"}
    assert db.get_trade_by_id(101)["exit"] is None
    assert isinstance(db.get_trade_by_id(102)["entry"], float)
    assert [row["id"] for row in db.get_all_trades_between(CHAT, today, today)] == [102, 103]
    assert [row["id"] for row in db.get_history(CHAT, 0)] == [102]

    events = db.get_trade_events_between(CHAT, today, today)
    assert [event["id"] for event in events] == [202]
    assert db.get_events_by_trade(CHAT)[101][0]["from_status"] is None
    assert [event["id"] for event in db.get_trade_events(101)] == [201]


def test_tick_explicit_utc_timestamp_and_order_contract(users_backend, monkeypatch):
    fixed = iter((
        datetime(2026, 7, 14, 12, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 14, 12, 0, 2, tzinfo=timezone.utc),
    ))
    monkeypatch.setattr(db, "_today", lambda: "2026-07-14")
    monkeypatch.setattr(db, "_utc_timestamp", lambda moment=None: next(fixed).strftime("%Y-%m-%d %H:%M:%S"))

    db.add_tick(CHAT, "AAPL", None, 0.25)
    db.add_tick(CHAT, "AAPL", 12.5, None)
    assert db.get_today_ticks(CHAT) == {"AAPL": [
        {"ts": "2026-07-14 12:00:01", "price": None, "strength": 0.25},
        {"ts": "2026-07-14 12:00:02", "price": 12.5, "strength": None},
    ]}


def test_get_or_create_user_is_idempotent(users_backend):
    user_id = CHAT + 1
    first = db.get_or_create_user(user_id, "first-name")
    second = db.get_or_create_user(user_id, "ignored-name")
    assert first == second
    assert first["username"] == "first-name"
    assert first["onboarding_state"] == "in_progress"
    with db._database().transaction() as transaction:
        timestamps = transaction.one(
            "SELECT created_at, updated_at FROM users WHERE user_id = :user_id",
            {"user_id": user_id},
        )
    datetime.strptime(timestamps["created_at"], "%Y-%m-%d %H:%M:%S")
    datetime.strptime(timestamps["updated_at"], "%Y-%m-%d %H:%M:%S")


def test_simple_user_setters_roundtrip(users_backend):
    db.set_user_active(CHAT, False)
    db.set_market_region(CHAT, "dax")
    assert db.set_trade_size(CHAT, 222.25) == 222.25
    db.set_top_n(CHAT, 9)
    db.set_sl_tp_mode(CHAT, "passiv")
    db.set_leverage(CHAT, 3.0)
    db.set_auto_accept(CHAT, True)
    db.set_auto_universe(CHAT, False)
    db.set_strategy(CHAT, "momentum")
    db.set_llm_rank(CHAT, False)
    db.set_eod_close(CHAT, True)
    db.set_signal_window(CHAT, True)
    db.set_broker_exec(CHAT, False)
    assert db.set_notify_channel(CHAT, "web") == "web"
    assert db.set_asset_pref(CHAT, "options") == "options"

    user = db.get_user(CHAT)
    assert user is not None
    assert {
        "is_active": user["is_active"], "market_regions": user["market_regions"],
        "trade_size_eur": user["trade_size_eur"], "top_n_signals": user["top_n_signals"],
        "sl_tp_mode": user["sl_tp_mode"], "leverage": user["leverage"],
        "auto_accept": user["auto_accept"], "auto_universe": user["auto_universe"],
        "strategies": user["strategies"], "llm_rank": user["llm_rank"],
        "eod_close": user["eod_close"], "signal_window": user["signal_window"],
        "broker_exec": user["broker_exec"], "notify_channel": user["notify_channel"],
        "asset_pref": user["asset_pref"],
    } == {
        "is_active": False, "market_regions": ["dax"], "trade_size_eur": 222.25,
        "top_n_signals": 9, "sl_tp_mode": "passiv",
        "leverage": max(1.0, min(config.MAX_LEVERAGE, 3.0)),
        "auto_accept": True, "auto_universe": False, "strategies": ["momentum"],
        "llm_rank": False, "eod_close": True, "signal_window": True,
        "broker_exec": False, "notify_channel": "web", "asset_pref": "options",
    }


def test_profile_credentials_and_clear_roundtrip(users_backend):
    db.save_profile(
        CHAT, trade_size_eur=333.5, broker_platform="alpaca",
        broker_api_key="profile-key", broker_api_secret="profile-secret",
    )
    assert db.get_decrypted_credentials(CHAT) == ("profile-key", "profile-secret")

    db.set_alpaca_credentials(CHAT, "new-key", "new-secret")
    assert db.has_alpaca_credentials(CHAT) is True
    assert db.get_decrypted_credentials(CHAT) == ("new-key", "new-secret")
    with db._database().transaction() as transaction:
        raw = transaction.one(
            "SELECT broker_api_key, broker_api_secret FROM users WHERE user_id = :user_id",
            {"user_id": CHAT},
        )
    assert isinstance(raw["broker_api_key"], bytes)
    assert raw["broker_api_key"] != b"new-key"

    db.clear_alpaca_credentials(CHAT)
    assert db.has_alpaca_credentials(CHAT) is False
    assert db.get_decrypted_credentials(CHAT) is None
    assert db.get_user(CHAT)["broker_exec"] is False


def test_list_mutations_and_dashboard_tokens(users_backend):
    assert db.toggle_region(CHAT, "dax") == ["sp500", "emerging", "dax"]
    assert db.toggle_region(CHAT, "sp500") == ["emerging", "dax"]
    assert db.toggle_strategy(CHAT, "momentum") == ["standard", "momentum"]
    assert db.add_watchlist_tickers(CHAT, [" tsla ", "AAPL"]) == ["AAPL", "MSFT", "TSLA"]
    assert db.remove_watchlist_ticker(CHAT, "msft") == ["AAPL", "TSLA"]

    rotated = db.rotate_dashboard_token(CHAT)
    assert rotated != TOKEN
    assert db.get_or_create_dashboard_token(CHAT) == rotated
    assert db.get_or_create_dashboard_token(CHAT) == rotated
    assert db.get_user_by_token(rotated)["user_id"] == CHAT


def test_session_lifecycle_and_expiry(users_backend):
    valid = db.create_session(CHAT, days=1)
    expired = db.create_session(CHAT, days=-1)
    with db._database().transaction() as transaction:
        timestamps = transaction.one(
            "SELECT created_at, expires_at FROM sessions WHERE token = :token",
            {"token": db._hash_token(valid)},
        )
    datetime.strptime(timestamps["created_at"], "%Y-%m-%d %H:%M:%S")
    datetime.strptime(timestamps["expires_at"], "%Y-%m-%d %H:%M:%S")
    assert valid != expired
    assert db.user_id_for_session(valid) == CHAT
    assert db.user_id_for_session(expired) is None
    assert db.delete_expired_sessions() == 1

    db.delete_session(valid)
    assert db.user_id_for_session(valid) is None

    db.create_session(CHAT, days=1)
    db.create_session(CHAT, days=1)
    assert db.delete_user_sessions(CHAT) == 2


def test_notification_roundtrip_and_read_state(users_backend):
    first_id = db.add_notification(CHAT, "Signal", "NVDA", "trade")
    second_id = db.add_notification(CHAT, "Info")

    assert isinstance(first_id, int)
    assert second_id > first_id
    assert db.unread_count(CHAT) == 2
    latest = db.get_notifications(CHAT, limit=1)
    assert latest == [{
        "id": second_id, "ts": latest[0]["ts"],
        "type": "info", "title": "Info", "body": "", "read": False,
    }]
    datetime.strptime(db.get_notifications(CHAT)[0]["ts"], "%Y-%m-%d %H:%M:%S")

    db.mark_notifications_read(CHAT)
    assert db.unread_count(CHAT) == 0
    assert all(row["read"] is True for row in db.get_notifications(CHAT))


def test_strategy_config_upsert_updates_one_row(users_backend):
    first = db.upsert_strategy_config(
        "ai_adaptive", "AI v1", "first", {"threshold": 0.4}, enabled=True
    )
    second = db.upsert_strategy_config(
        "ai_adaptive", "AI v2", "second", {"threshold": 0.7}, enabled=False
    )

    assert first["params"] == {"threshold": 0.4}
    assert second == db.get_strategy_config("ai_adaptive")
    assert second["label"] == "AI v2"
    assert second["description"] == "second"
    assert second["params"] == {"threshold": 0.7}
    assert second["enabled"] is False
    datetime.strptime(second["updated_at"], "%Y-%m-%d %H:%M:%S")
    assert [row["key"] for row in db.list_strategy_configs()] == ["ai_adaptive"]


def test_lab_trade_read_api_applies_utc_cutoff(users_backend):
    today = datetime.now(timezone.utc).date()
    statement = (
        "INSERT INTO trades "
        "(user_id, trade_date, ticker, direction, signal_json, status, pnl_pct) "
        "VALUES (:user_id, :trade_date, :ticker, 'LONG', :signal_json, 'closed', :pnl_pct)"
    )
    with db._database().transaction() as transaction:
        transaction.execute(statement, {
            "user_id": CHAT, "trade_date": (today - timedelta(days=7)).isoformat(),
            "ticker": "IN", "signal_json": json.dumps({"strategy": lab.TARGET_KEY}),
            "pnl_pct": 1.25,
        })
        transaction.execute(statement, {
            "user_id": CHAT, "trade_date": (today - timedelta(days=8)).isoformat(),
            "ticker": "OUT", "signal_json": json.dumps({"strategy": lab.TARGET_KEY}),
            "pnl_pct": -2.0,
        })

    assert db.get_closed_trade_results_since(7) == [{
        "signal_json": json.dumps({"strategy": lab.TARGET_KEY}), "pnl_pct": 1.25,
    }]
    result = lab.reality_check(None, days=7, min_trades=1)
    assert result["n"] == 1
    assert result["live_win_rate"] == 100.0
