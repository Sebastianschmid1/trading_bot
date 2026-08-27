"""
Laufzeit-Persistenz für Multi-User-Betrieb (SQLite/PostgreSQL).

Trade-Lifecycle-Übergänge setzen zunächst READ COMMITTED voraus und schützen den Zustand
mit Compare-and-set-Updates (statusbewachtes ``UPDATE`` + ``rowcount``). Statusänderung und
Trade-Event teilen stets dieselbe Seam-Transaktion; Row Locks werden nicht eingesetzt, wo
CAS den einzigen Gewinner bereits eindeutig bestimmt.

SQLite-Persistenz für Multi-User-Betrieb
Ersetzt tracker.py — speichert Nutzerprofile (inkl. verschlüsselter
Broker-Zugangsdaten) und Demo-Trades, jeweils pro user_id (== Telegram chat_id).
"""

import sqlite3
import json
import hashlib
import logging
import secrets
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

from stockbot import config
from stockbot.config import ENCRYPTION_KEY, MAX_LEVERAGE
from stockbot.core import db_backend
from stockbot.paths import DATA_DIR

log = logging.getLogger(__name__)
DB_FILE = DATA_DIR / "bot.db"


class _SignalQuoteSource:
    """Kurschnittstelle für die Trade-Aktivierung (Einstiegskurs). Liefert `Ticker(t).fast_info.
    last_price` aus dem PRODUKTIONS-Signalprovider (Alpaca, nie yfinance — Leitplanke W3.2) und
    bewahrt dabei die bestehende Test-Naht ``db.yf`` (Tests ersetzen ``db.yf`` bzw. patchen
    ``db.yf.Ticker`` weiterhin, ohne dass yfinance im Prod-Pfad landet)."""

    class _FastInfo:
        def __init__(self, price):
            self.last_price = price

    class _Ticker:
        def __init__(self, price):
            self.fast_info = _SignalQuoteSource._FastInfo(price)

    def Ticker(self, ticker):
        from stockbot.market import provider_factory
        price = provider_factory.get_signal_provider().get_quote(ticker).price
        return self._Ticker(float(price))


# ``yf`` bleibt der Name der Kursnaht (Rückwärtskompatibilität für Tests), zeigt aber NICHT mehr
# auf das yfinance-Modul, sondern auf den Alpaca-gestützten Signalprovider (Leitplanke W3.2).
yf = _SignalQuoteSource()

_fernet = Fernet(ENCRYPTION_KEY.encode())

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id           INTEGER PRIMARY KEY,
    username          TEXT,
    trade_size_eur    REAL    NOT NULL DEFAULT 25.0,
    broker_platform   TEXT,
    broker_api_key    BLOB,
    broker_api_secret BLOB,
    onboarding_state  TEXT    NOT NULL DEFAULT 'in_progress',
    is_active         INTEGER NOT NULL DEFAULT 1,
    dashboard_token   TEXT,
    market_region     TEXT    NOT NULL DEFAULT 'sp500',
    top_n_signals     INTEGER NOT NULL DEFAULT 5,
    sl_tp_mode        TEXT    NOT NULL DEFAULT 'normal',
    leverage          REAL    NOT NULL DEFAULT 1.0,
    auto_accept       INTEGER NOT NULL DEFAULT 0,
    auto_universe     INTEGER NOT NULL DEFAULT 1,
    strategy          TEXT    NOT NULL DEFAULT 'standard',
    llm_rank          INTEGER NOT NULL DEFAULT 1,
    eod_close         INTEGER NOT NULL DEFAULT 1,
    broker_exec       INTEGER NOT NULL DEFAULT 0,
    signal_window     INTEGER NOT NULL DEFAULT 0,
    watchlist         TEXT    NOT NULL DEFAULT '',
    notify_channel    TEXT    NOT NULL DEFAULT 'both',
    asset_pref        TEXT    NOT NULL DEFAULT 'stocks',
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(user_id),
    trade_date   TEXT    NOT NULL,
    ticker       TEXT    NOT NULL,
    direction    TEXT    NOT NULL,
    signal_json  TEXT    NOT NULL,
    message_id   INTEGER,
    status       TEXT    NOT NULL DEFAULT 'pending',
    entry        REAL,
    exit         REAL,
    pnl_eur      REAL,
    pnl_pct      REAL,
    broker_order_id          TEXT,
    broker_status            TEXT,
    broker_filled_qty        REAL,
    broker_filled_avg_price  REAL,
    broker_updated_at        TEXT,
    high_water               REAL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, trade_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_trades_user_date_status
    ON trades (user_id, trade_date, status);

CREATE TABLE IF NOT EXISTS trade_ticks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    trade_date  TEXT    NOT NULL,
    ticker      TEXT    NOT NULL,
    ts          TEXT    NOT NULL DEFAULT (datetime('now')),
    price       REAL,
    strength    REAL
);

CREATE INDEX IF NOT EXISTS idx_ticks_user_date_ticker
    ON trade_ticks (user_id, trade_date, ticker, ts);

CREATE TABLE IF NOT EXISTS trades_archive (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(user_id),
    trade_date   TEXT    NOT NULL,
    ticker       TEXT    NOT NULL,
    direction    TEXT    NOT NULL,
    signal_json  TEXT    NOT NULL,
    message_id   INTEGER,
    status       TEXT    NOT NULL DEFAULT 'pending',
    entry        REAL,
    exit         REAL,
    pnl_eur      REAL,
    pnl_pct      REAL,
    broker_order_id          TEXT,
    broker_status            TEXT,
    broker_filled_qty        REAL,
    broker_filled_avg_price  REAL,
    broker_updated_at        TEXT,
    high_water               REAL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    archived_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    archive_reason TEXT   NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_archive_user_date_status
    ON trades_archive (user_id, trade_date, status);

CREATE TABLE IF NOT EXISTS trade_ticks_archive (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    trade_date  TEXT    NOT NULL,
    ticker      TEXT    NOT NULL,
    ts          TEXT    NOT NULL DEFAULT (datetime('now')),
    price       REAL,
    strength    REAL,
    archived_at TEXT    NOT NULL DEFAULT (datetime('now')),
    archive_reason TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ticks_archive_user_date_ticker
    ON trade_ticks_archive (user_id, trade_date, ticker, ts);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(user_id),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    ts          TEXT    NOT NULL DEFAULT (datetime('now')),
    type        TEXT    NOT NULL DEFAULT 'info',
    title       TEXT    NOT NULL,
    body        TEXT    NOT NULL DEFAULT '',
    read        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS strategy_configs (
    key          TEXT PRIMARY KEY,
    label        TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    params_json  TEXT    NOT NULL DEFAULT '{}',
    enabled      INTEGER NOT NULL DEFAULT 1,
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications (user_id, read, id);

CREATE TABLE IF NOT EXISTS trade_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    ticker        TEXT    NOT NULL,
    trade_date    TEXT    NOT NULL,
    from_status   TEXT,
    to_status     TEXT    NOT NULL,
    broker_status TEXT,
    ts            TEXT    NOT NULL DEFAULT (datetime('now')),
    note          TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_events_trade ON trade_events (trade_id, ts);
CREATE INDEX IF NOT EXISTS idx_trade_events_user  ON trade_events (user_id, ts);

CREATE TABLE IF NOT EXISTS trade_intents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    signal_id            INTEGER NOT NULL,
    requested_action     TEXT    NOT NULL,
    accepted_exit_policy TEXT    NOT NULL,
    source_channel       TEXT    NOT NULL,
    created_at           TEXT    NOT NULL,
    idempotency_key      TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS orders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_intent_id   INTEGER NOT NULL REFERENCES trade_intents(id),
    user_id           INTEGER NOT NULL,
    ticker            TEXT    NOT NULL,
    side              TEXT    NOT NULL,
    qty               REAL,
    notional          REAL,
    limit_price       REAL,
    status            TEXT    NOT NULL DEFAULT 'created',
    broker_order_id   TEXT,
    client_order_id   TEXT    UNIQUE,
    idempotency_key   TEXT    NOT NULL UNIQUE,
    rejection_reason  TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders(id),
    event_type      TEXT    NOT NULL,
    from_status     TEXT,
    to_status       TEXT    NOT NULL,
    broker_event_id TEXT,
    payload_json    TEXT    NOT NULL DEFAULT '{}',
    occurred_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events (order_id, id);

CREATE TABLE IF NOT EXISTS protective_orders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_order_id   INTEGER NOT NULL REFERENCES orders(id),
    trade_intent_id   INTEGER NOT NULL REFERENCES trade_intents(id),
    user_id           INTEGER NOT NULL,
    ticker            TEXT    NOT NULL,
    side              TEXT    NOT NULL,
    qty               REAL    NOT NULL,
    stop_price        REAL    NOT NULL,
    status            TEXT    NOT NULL,
    broker_order_id   TEXT    NOT NULL UNIQUE,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT    NOT NULL UNIQUE,
    timestamp      TEXT    NOT NULL,
    user_id        INTEGER,
    actor          TEXT    NOT NULL,
    entity_type    TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    action         TEXT    NOT NULL,
    old_state      TEXT,
    new_state      TEXT,
    trace_id       TEXT    NOT NULL,
    source_channel TEXT    NOT NULL,
    metadata_json  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_entity
    ON audit_events (entity_type, entity_id, id);

CREATE TABLE IF NOT EXISTS kill_switches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT    NOT NULL,
    user_id         INTEGER,
    active          INTEGER NOT NULL,
    reason          TEXT    NOT NULL,
    activated_by    TEXT    NOT NULL,
    activated_at    TEXT    NOT NULL,
    deactivated_by  TEXT,
    deactivated_at  TEXT,
    CHECK (scope IN ('global', 'user')),
    CHECK ((scope = 'global' AND user_id IS NULL) OR
           (scope = 'user' AND user_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_kill_switches_active
    ON kill_switches (active, scope, user_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_kill_switches_active_global
    ON kill_switches (scope) WHERE active = 1 AND scope = 'global';

CREATE UNIQUE INDEX IF NOT EXISTS uq_kill_switches_active_user
    ON kill_switches (user_id) WHERE active = 1 AND scope = 'user';

CREATE TABLE IF NOT EXISTS broker_oauth_connections (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    mode           TEXT    NOT NULL,
    access_token   BLOB    NOT NULL,
    refresh_token  BLOB,
    scopes         TEXT    NOT NULL,
    expires_at     TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    revoked_at     TEXT,
    CHECK (mode IN ('paper', 'live'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_oauth_user_mode
    ON broker_oauth_connections (user_id, mode);

CREATE TABLE IF NOT EXISTS outbox_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         TEXT    NOT NULL UNIQUE,
    event_type       TEXT    NOT NULL,
    version          INTEGER NOT NULL,
    trace_id         TEXT    NOT NULL,
    payload_json     TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'pending',
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 5,
    next_attempt_at  TEXT    NOT NULL,
    last_error       TEXT,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    CHECK (status IN ('pending', 'delivered', 'dead'))
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON outbox_events (status, next_attempt_at, id);

CREATE TABLE IF NOT EXISTS callback_tokens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token         TEXT    NOT NULL UNIQUE,
    user_id       INTEGER NOT NULL,
    action        TEXT    NOT NULL,
    payload_json  TEXT    NOT NULL,
    expires_at    TEXT    NOT NULL,
    used_at       TEXT,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_callback_tokens_token
    ON callback_tokens (token);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_key     TEXT    NOT NULL,
    strategy_id      INTEGER NOT NULL,
    version          INTEGER NOT NULL,
    params_json      TEXT    NOT NULL DEFAULT '{}',
    feature_version  TEXT    NOT NULL DEFAULT 'unversioned',
    universe_version TEXT    NOT NULL DEFAULT 'unversioned',
    entry_rules      TEXT    NOT NULL DEFAULT '',
    exit_rules       TEXT    NOT NULL DEFAULT '',
    cost_model_json  TEXT    NOT NULL DEFAULT '{}',
    release_status   TEXT    NOT NULL DEFAULT 'live',
    code_commit      TEXT    NOT NULL DEFAULT '',
    content_hash     TEXT    NOT NULL,
    created_at       TEXT    NOT NULL,
    UNIQUE (strategy_key, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_strategy_versions_key
    ON strategy_versions (strategy_key);

CREATE TABLE IF NOT EXISTS risk_profiles (
    user_id                        INTEGER PRIMARY KEY REFERENCES users(user_id),
    account_risk_per_trade_pct     REAL    NOT NULL DEFAULT 0.25,
    daily_loss_limit_pct           REAL    NOT NULL DEFAULT 1.00,
    max_open_positions             INTEGER NOT NULL DEFAULT 5,
    max_position_pct               REAL    NOT NULL DEFAULT 100.0,
    max_sector_exposure_pct        REAL    NOT NULL DEFAULT 100.0,
    max_correlated_exposure_pct    REAL    NOT NULL DEFAULT 100.0,
    max_daily_new_exposure_pct     REAL    NOT NULL DEFAULT 100.0,
    max_spread_bps                 REAL    NOT NULL DEFAULT 50.0,
    max_quote_age_seconds          INTEGER NOT NULL DEFAULT 60,
    min_average_dollar_volume      REAL    NOT NULL DEFAULT 0.0,
    earnings_blackout_days         INTEGER NOT NULL DEFAULT 0,
    allow_overnight                INTEGER NOT NULL DEFAULT 1,
    allowed_strategies_json        TEXT    NOT NULL DEFAULT '[]',
    created_at                     TEXT    NOT NULL,
    updated_at                     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_data_archive (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    trading_date  TEXT    NOT NULL,
    timeframe     TEXT    NOT NULL,
    provider      TEXT    NOT NULL,
    fetched_at    TEXT    NOT NULL,
    row_count     INTEGER NOT NULL,
    file_path     TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    UNIQUE (symbol, trading_date, timeframe)
);

CREATE TABLE IF NOT EXISTS shadow_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_version_id INTEGER NOT NULL,
    ticker              TEXT    NOT NULL,
    captured_at         TEXT    NOT NULL,
    net_pnl             REAL,
    open_risk           REAL,
    created_at          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_snapshots_captured
    ON shadow_snapshots (captured_at);

CREATE TABLE IF NOT EXISTS polymarket_markets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id        TEXT    NOT NULL UNIQUE,
    token_id            TEXT    NOT NULL DEFAULT '',
    slug                TEXT    NOT NULL DEFAULT '',
    question            TEXT    NOT NULL DEFAULT '',
    resolution_source   TEXT    NOT NULL DEFAULT '',
    resolution_rules    TEXT    NOT NULL DEFAULT '',
    end_date            TEXT,
    category            TEXT,
    first_seen_at       TEXT    NOT NULL,
    last_seen_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS polymarket_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id        TEXT    NOT NULL,
    fetched_at          TEXT    NOT NULL,
    bid                 REAL,
    ask                 REAL,
    mid                 REAL,
    spread_bps          REAL,
    depth_bid_usd       REAL,
    depth_ask_usd       REAL,
    volume_24h_usd      REAL,
    liquidity_usd       REAL,
    trade_count_24h     INTEGER,
    last_trade_at       TEXT,
    usable              INTEGER NOT NULL DEFAULT 0,
    reject_code         TEXT    NOT NULL DEFAULT '',
    raw_file_path       TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_polymarket_snapshots_condition
    ON polymarket_snapshots (condition_id, fetched_at);
"""


def init_db():
    """Legt data/-Ordner und Tabellen an (idempotent). Beim Start einmal aufrufen."""
    # Der Cache lebt in `strategies` — hier wird er dort zurückgesetzt, nicht über das Paket:
    # ein rebindender Schreibzugriff träfe sonst nur die Kopie im Re-Export.
    strategies._STRATEGY_VERSION_CACHE.clear()   # frische DB → gecachte Strategie-Version-IDs verwerfen
    strategies._STRATEGY_VERSIONS_BOOTSTRAPPED = False
    if config.DB_BACKEND == "postgres":
        _migrate()
        return
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)


_EXPECTED_POSTGRES_TABLES = frozenset({
    "users", "trades", "trade_ticks", "trades_archive", "trade_ticks_archive",
    "sessions", "notifications", "strategy_configs", "trade_events", "trade_intents",
    "orders", "order_events", "audit_events", "kill_switches",
})


def _check_postgres_schema_readiness() -> None:
    """Read-only startup guard; PostgreSQL schema ownership stays exclusively with Alembic."""
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
    present = {row["table_name"] for row in rows}
    missing = sorted(_EXPECTED_POSTGRES_TABLES - present)
    if missing:
        raise RuntimeError(
            "PostgreSQL-Schema nicht bereit; fehlende Tabellen: "
            f"{', '.join(missing)}. Alembic upgrade head ausführen."
        )


def _migrate(conn: sqlite3.Connection | None = None):
    """Additive Schema-Migrationen für bestehende Datenbanken (idempotent)."""
    if config.DB_BACKEND == "postgres":
        _check_postgres_schema_readiness()
        _migrate_leverage_values()
        return
    if conn is None:
        raise ValueError("SQLite-Migration benötigt eine SQLite-Verbindung")
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "dashboard_token" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN dashboard_token TEXT")
        log.info("Migration: Spalte users.dashboard_token ergänzt.")
    if "market_region" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN market_region TEXT NOT NULL DEFAULT 'sp500'")
        log.info("Migration: Spalte users.market_region ergänzt.")
    if "top_n_signals" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN top_n_signals INTEGER NOT NULL DEFAULT 5")
        log.info("Migration: Spalte users.top_n_signals ergänzt.")
    if "sl_tp_mode" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN sl_tp_mode TEXT NOT NULL DEFAULT 'normal'")
        log.info("Migration: Spalte users.sl_tp_mode ergänzt.")
    if "leverage" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN leverage REAL NOT NULL DEFAULT 1.0")
        log.info("Migration: Spalte users.leverage ergänzt.")
    if "auto_accept" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN auto_accept INTEGER NOT NULL DEFAULT 0")
        log.info("Migration: Spalte users.auto_accept ergänzt.")
    if "auto_universe" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN auto_universe INTEGER NOT NULL DEFAULT 1")
        log.info("Migration: Spalte users.auto_universe ergänzt.")
    if "strategy" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN strategy TEXT NOT NULL DEFAULT 'standard'")
        log.info("Migration: Spalte users.strategy ergänzt.")
    if "llm_rank" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN llm_rank INTEGER NOT NULL DEFAULT 1")
        log.info("Migration: Spalte users.llm_rank ergänzt.")
    if "eod_close" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN eod_close INTEGER NOT NULL DEFAULT 1")
        log.info("Migration: Spalte users.eod_close ergänzt.")
    if "broker_exec" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN broker_exec INTEGER NOT NULL DEFAULT 0")
        log.info("Migration: Spalte users.broker_exec ergänzt.")
    if "watchlist" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN watchlist TEXT NOT NULL DEFAULT ''")
        log.info("Migration: Spalte users.watchlist ergänzt.")
    if "notify_channel" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN notify_channel TEXT NOT NULL DEFAULT 'both'")
        log.info("Migration: Spalte users.notify_channel ergänzt.")
    if "asset_pref" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN asset_pref TEXT NOT NULL DEFAULT 'stocks'")
        log.info("Migration: Spalte users.asset_pref ergänzt.")
    if "signal_window" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN signal_window INTEGER NOT NULL DEFAULT 0")
        log.info("Migration: Spalte users.signal_window ergänzt.")

    strategy_cols = {row["name"] for row in conn.execute("PRAGMA table_info(strategy_configs)").fetchall()}
    if not strategy_cols:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_configs (
                   key          TEXT PRIMARY KEY,
                   label        TEXT    NOT NULL,
                   description  TEXT    NOT NULL DEFAULT '',
                   params_json  TEXT    NOT NULL DEFAULT '{}',
                   enabled      INTEGER NOT NULL DEFAULT 1,
                   updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        strategy_cols = {row["name"] for row in conn.execute("PRAGMA table_info(strategy_configs)").fetchall()}
        log.info("Migration: Tabelle strategy_configs ergänzt.")
    for name, ddl in {
        "label": "ALTER TABLE strategy_configs ADD COLUMN label TEXT NOT NULL DEFAULT ''",
        "description": "ALTER TABLE strategy_configs ADD COLUMN description TEXT NOT NULL DEFAULT ''",
        "params_json": "ALTER TABLE strategy_configs ADD COLUMN params_json TEXT NOT NULL DEFAULT '{}'",
        "enabled": "ALTER TABLE strategy_configs ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
        "updated_at": "ALTER TABLE strategy_configs ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'))",
    }.items():
        if name not in strategy_cols:
            try:
                conn.execute(ddl)
                log.info(f"Migration: Spalte strategy_configs.{name} ergänzt.")
            except Exception:
                pass

    trade_cols = {row["name"] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    for name, ddl in {
        "broker_order_id": "ALTER TABLE trades ADD COLUMN broker_order_id TEXT",
        "broker_status": "ALTER TABLE trades ADD COLUMN broker_status TEXT",
        "broker_filled_qty": "ALTER TABLE trades ADD COLUMN broker_filled_qty REAL",
        "broker_filled_avg_price": "ALTER TABLE trades ADD COLUMN broker_filled_avg_price REAL",
        "broker_updated_at": "ALTER TABLE trades ADD COLUMN broker_updated_at TEXT",
        # Hoechstkurs seit Einstieg — Grundlage des ATR-Trailing-Stops (market/exit_policies).
        # Bewusst eine eigene Spalte statt einer Auswertung von `trade_ticks`: die Tick-Tabelle
        # ist nach `trade_date` partitioniert und wird beim Archivieren geleert, taugt also
        # nicht als Grundlage fuer einen Exit, der echtes Geld bewegt.
        "high_water": "ALTER TABLE trades ADD COLUMN high_water REAL",
    }.items():
        if name not in trade_cols:
            conn.execute(ddl)
            log.info(f"Migration: Spalte trades.{name} ergänzt.")

    # Die Archivtabelle spiegelt `trades` spaltengleich (Reihenfolge inklusive) — die
    # Archivierungs-Query listet die Spalten einzeln auf und bricht sonst.
    archive_cols = {row["name"] for row in conn.execute("PRAGMA table_info(trades_archive)").fetchall()}
    if archive_cols and "high_water" not in archive_cols:
        conn.execute("ALTER TABLE trades_archive ADD COLUMN high_water REAL")
        log.info("Migration: Spalte trades_archive.high_water ergänzt.")

    # Status-Event-Log (Teil A): Tabelle anlegen und für Alt-Trades einmalig backfillen,
    # damit auch historische Trades grobe Status-Dauern haben.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trade_events (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               trade_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
               ticker TEXT NOT NULL, trade_date TEXT NOT NULL,
               from_status TEXT, to_status TEXT NOT NULL, broker_status TEXT,
               ts TEXT NOT NULL DEFAULT (datetime('now')), note TEXT
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_events_trade ON trade_events (trade_id, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_events_user  ON trade_events (user_id, ts)")
    have_events = conn.execute("SELECT 1 FROM trade_events LIMIT 1").fetchone()
    if not have_events:
        n = 0
        for t in conn.execute(
            "SELECT id, user_id, ticker, trade_date, status, broker_status, "
            "created_at, broker_updated_at FROM trades"
        ).fetchall():
            # 1) Anlage-Event (created → 'pending') am created_at.
            conn.execute(
                """INSERT INTO trade_events (trade_id, user_id, ticker, trade_date,
                                             from_status, to_status, broker_status, ts, note)
                   VALUES (?, ?, ?, ?, NULL, 'pending', NULL, ?, 'backfill')""",
                (t["id"], t["user_id"], t["ticker"], t["trade_date"],
                 t["created_at"] or t["trade_date"]),
            )
            n += 1
            # 2) Abschluss-Event für terminale Trades am broker_updated_at (sofern vorhanden).
            if t["status"] in ("closed", "broker_failed", "rejected", "expired") and t["broker_updated_at"]:
                conn.execute(
                    """INSERT INTO trade_events (trade_id, user_id, ticker, trade_date,
                                                 from_status, to_status, broker_status, ts, note)
                       VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, 'backfill')""",
                    (t["id"], t["user_id"], t["ticker"], t["trade_date"], t["status"],
                     t["broker_status"], t["broker_updated_at"]),
                )
        if n:
            log.info(f"Migration: trade_events backfilled für {n} Trade(s).")

    # Web-Session-Tokens nur noch als SHA-256-Hash speichern: Klartext-Alttokens einmalig
    # hashen (erkennbar an der Form — token_urlsafe(32) ist 43 Zeichen Base64, kein 64er-Hex).
    legacy = [r["token"] for r in conn.execute("SELECT token FROM sessions").fetchall()
              if not _is_token_hash(r["token"])]
    for tok in legacy:
        conn.execute("UPDATE sessions SET token = ? WHERE token = ?", (_hash_token(tok), tok))
    if legacy:
        log.info(f"Migration: {len(legacy)} Web-Session-Token(s) gehasht.")

    _migrate_leverage_values(conn)

    # Phase 4 / OMS: additive, idempotente Tabellen. Bei Alt-Datenbanken wurde
    # SCHEMA_SQL bereits ausgefuehrt; die explizite Anlage hier dokumentiert den
    # Migrationspfad und haelt direkte _migrate-Aufrufe rueckwaertskompatibel.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_intents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL, requested_action TEXT NOT NULL,
            accepted_exit_policy TEXT NOT NULL, source_channel TEXT NOT NULL,
            created_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_intent_id INTEGER NOT NULL REFERENCES trade_intents(id),
            user_id INTEGER NOT NULL, ticker TEXT NOT NULL, side TEXT NOT NULL,
            qty REAL, notional REAL, limit_price REAL,
            status TEXT NOT NULL DEFAULT 'created', broker_order_id TEXT,
            client_order_id TEXT UNIQUE, idempotency_key TEXT NOT NULL UNIQUE,
            rejection_reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id), event_type TEXT NOT NULL,
            from_status TEXT, to_status TEXT NOT NULL, broker_event_id TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events (order_id, id);
    """)


def _migrate_leverage_values(conn: sqlite3.Connection | None = None):
    """TSAFE-002: klemmt bestehende Hebel-Altwerte > `MAX_LEVERAGE` auf `MAX_LEVERAGE` — sowohl
    `users.leverage` als auch der Hebel im gespeicherten `signal_json` noch offener Trades
    (pending/active/broker_pending). Läuft bei jedem Start erneut (idempotent) und heilt so auch
    Datensätze, die vor der Einführung des harten Server-Caps in `set_leverage`/`set_trade_leverage`
    entstanden sind. Abgeschlossene Trades bleiben als historischer Datensatz unverändert."""
    # Startup is operationally single-run. The updates are nevertheless idempotent so a
    # repeated process start is harmless on both backends.
    if conn is not None:
        transaction = db_backend._SqliteTransaction(conn)
        manager = nullcontext(transaction)
    else:
        manager = _database().transaction()
    with manager as transaction:
        n_users = transaction.execute(
            "UPDATE users SET leverage = :cap WHERE leverage > :cap", {"cap": MAX_LEVERAGE}
        )
        rows = transaction.all(
            "SELECT id, signal_json FROM trades "
            "WHERE status IN ('pending', 'active', 'broker_pending')"
        )
        n_trades = 0
        for row in rows:
            try:
                sig = json.loads(row["signal_json"])
            except Exception:
                continue
            changed = False
            for key in ("leverage", "effective_leverage"):
                val = sig.get(key)
                if val is not None and float(val) > MAX_LEVERAGE:
                    sig[key] = MAX_LEVERAGE
                    changed = True
            if changed:
                transaction.execute(
                    "UPDATE trades SET signal_json = :signal_json WHERE id = :trade_id",
                    {"signal_json": json.dumps(sig), "trade_id": row["id"]},
                )
                n_trades += 1
    if n_users:
        log.info(f"Migration: {n_users} users.leverage-Altwert(e) > {MAX_LEVERAGE:g}x auf {MAX_LEVERAGE:g}x geklemmt.")
    if n_trades:
        log.info(f"Migration: {n_trades} offene(r) Trade(s) mit Hebel-Altwert "
                 f"> {MAX_LEVERAGE:g}x auf {MAX_LEVERAGE:g}x geklemmt.")

@contextmanager
def _connect():
    """Öffnet eine SQLite-Verbindung mit Spaltenzugriff per Name; committet & schließt automatisch."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def today_utc_date() -> date:
    """Der heutige Handelstag als `date` — **UTC**, nicht Server-Lokalzeit.

    Die eine Wahrheit für „welcher Tag ist heute" in der gesamten Anwendung (Backtests
    haben mit `backtest/clock.py` bewusst einen eigenen Zeitbegriff). Alle Zeitstempel in
    der DB folgen dem naiven UTC-Vertrag (`_utc_timestamp()`), `trade_date` ebenso.
    `date.today()` folgt dagegen der Server-Zeitzone: auf einer Maschine mit Offset
    (z. B. CEST = UTC+2) liegt der lokale Tag zwischen Mitternacht und dem Offset einen
    Tag vor/nach dem UTC-Tag — wer damit gegen DB-Werte vergleicht, greift ins Leere.
    Produktion (VPS, `Etc/UTC`) verhält sich unverändert.
    """
    return datetime.now(timezone.utc).date()


def today_utc() -> str:
    """Derselbe Handelstag als ISO-String 'YYYY-MM-DD' (Format der `trade_date`-Spalte)."""
    return str(today_utc_date())


def _today() -> str:
    """DB-interner Name für `today_utc()` (stempelt die `trade_date`-Spalte)."""
    return today_utc()


def _log_event(transaction: db_backend.DbTransaction, *, trade_id: int, user_id: int, ticker: str,
               trade_date: str, from_status: str | None, to_status: str,
               broker_status: str | None = None, note: str | None = None) -> None:
    """Schreibt einen Status-Übergang in `trade_events` — in DERSELBEN Transaktion wie der
    auslösende UPDATE/INSERT. Reine Wiederholungen (gleicher Status) werden übersprungen, damit
    der periodische Monitor (broker_pending→broker_pending) den Log nicht zumüllt."""
    if from_status == to_status:
        return
    transaction.execute(
        """INSERT INTO trade_events (trade_id, user_id, ticker, trade_date,
                                     from_status, to_status, broker_status, ts, note)
           VALUES (:trade_id, :user_id, :ticker, :trade_date, :from_status, :to_status,
                   :broker_status, :ts, :note)""",
        {"trade_id": trade_id, "user_id": user_id, "ticker": ticker,
         "trade_date": trade_date, "from_status": from_status, "to_status": to_status,
         # Zeitvertrag: Der Schema-Default darf auf PostgreSQL nie greifen (tz-aware String).
         "broker_status": broker_status, "ts": _utc_timestamp(), "note": note},
    )


# ── Verschlüsselung ─────────────────────────────────────────────────────────

def encrypt(plaintext: str) -> bytes:
    """Verschlüsselt einen String zur Speicherung (z. B. Broker-API-Key/-Secret)."""
    return _fernet.encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Entschlüsselt aus der DB gelesene Bytes zurück zum ursprünglichen String."""
    return _fernet.decrypt(bytes(ciphertext)).decode("utf-8")


def _utc_timestamp(moment: datetime | None = None) -> str:
    """SQLite-compatible UTC TEXT timestamp (seconds precision)."""
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")


def _database():
    return db_backend.get_database(config.DB_BACKEND, _connect)


def set_trade_leverage(user_id: int, ticker: str, leverage: float):
    """Ändert den Hebel eines noch ausstehenden Trades (im gespeicherten signal_json) —
    serverseitig hart auf `MAX_LEVERAGE` begrenzt (TSAFE-002, Default 1×)."""
    leverage = max(1.0, min(MAX_LEVERAGE, float(leverage)))
    with _database().transaction() as transaction:
        row = transaction.one(
            """SELECT signal_json FROM trades
               WHERE user_id = :user_id AND trade_date = :trade_date AND ticker = :ticker""",
            {"user_id": user_id, "trade_date": _today(), "ticker": ticker},
        )
        if not row:
            return
        sig = json.loads(row["signal_json"])
        sig["leverage"] = leverage
        transaction.execute(
            """UPDATE trades SET signal_json = :signal_json
               WHERE user_id = :user_id AND trade_date = :trade_date AND ticker = :ticker""",
            {"signal_json": json.dumps(sig, default=str), "user_id": user_id,
             "trade_date": _today(), "ticker": ticker},
        )


def merge_active_trade_signal(user_id: int, ticker: str, extra: dict) -> None:
    """Ergänzt das gespeicherte Signal eines aktiven Trades um zusätzliche Felder (z. B. den
    gewählten Optionskontrakt: option_symbol/entry_premium/delta/omega/contracts). Idempotent."""
    if not extra:
        return
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, signal_json FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status IN ('active', 'broker_pending') "
            "ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return
        sig = json.loads(row["signal_json"])
        sig.update(extra)
        transaction.execute(
            "UPDATE trades SET signal_json = :signal_json WHERE id = :id",
            {"signal_json": json.dumps(sig, default=str), "id": row["id"]},
        )


def reset_user_trades(user_id: int) -> int:
    """Leert den sichtbaren Trading-Verlauf eines Nutzers.

    Trades jedes Status und ihre Intraday-Ticks werden in derselben Transaktion mit Grund
    ``user_reset`` archiviert und erst danach aus den Live-Tabellen entfernt. Das Archiv ist
    reine Audit-/Forschungsaufbewahrung und wird von Dashboard/Reports derzeit nicht gelesen.
    In-App-Mitteilungen werden weiterhin endgültig gelöscht: Sie sind UI-Zustellungen und
    keine Performance-Daten. Profil, Einstellungen und Broker-Zugangsdaten bleiben erhalten.
    Gibt die Zahl der aus den Live-Tabellen entfernten Zeilen zurück.
    """
    archived_at = _utc_timestamp()
    with _database().transaction() as transaction:
        transaction.execute(
            """INSERT INTO trades_archive
               (id, user_id, trade_date, ticker, direction, signal_json, message_id, status,
                entry, exit, pnl_eur, pnl_pct, broker_order_id, broker_status,
                broker_filled_qty, broker_filled_avg_price, broker_updated_at, high_water,
                created_at, archived_at, archive_reason)
               SELECT id, user_id, trade_date, ticker, direction, signal_json, message_id, status,
                      entry, exit, pnl_eur, pnl_pct, broker_order_id, broker_status,
                      broker_filled_qty, broker_filled_avg_price, broker_updated_at, high_water,
                      created_at, :archived_at, 'user_reset'
                 FROM trades WHERE user_id = :user_id""",
            {"user_id": user_id, "archived_at": archived_at},
        )
        transaction.execute(
            """INSERT INTO trade_ticks_archive
               (id, user_id, trade_date, ticker, ts, price, strength, archived_at, archive_reason)
               SELECT id, user_id, trade_date, ticker, ts, price, strength,
                      :archived_at, 'user_reset'
                 FROM trade_ticks WHERE user_id = :user_id""",
            {"user_id": user_id, "archived_at": archived_at},
        )
        total = 0
        for table in ("trades", "trade_ticks", "notifications"):
            total += transaction.execute(
                f"DELETE FROM {table} WHERE user_id = :user_id", {"user_id": user_id}
            )
    log.info(
        f"Reset: user_id={user_id} — Trades/Ticks archiviert; "
        f"{total} Live-/UI-Zeile(n) entfernt."
    )
    return total


def get_closed_trade_results_since(days: int = 45) -> list[dict]:
    """Rohdaten geschlossener Trades seit dem UTC-Tages-Cutoff (inklusive)."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=int(days))).isoformat()
    with _database().transaction() as transaction:
        return transaction.all(
            "SELECT signal_json, pnl_pct FROM trades WHERE status = 'closed' "
            "AND pnl_pct IS NOT NULL AND trade_date >= :cutoff",
            {"cutoff": cutoff},
        )


# ── Trade-Tracking (ersetzt TradeTracker, jetzt pro user_id) ───────────────

def _trade_to_dict(row) -> dict:
    out = {
        "id":         row["id"] if "id" in row.keys() else None,
        "user_id":    row["user_id"] if "user_id" in row.keys() else None,
        "ticker":     row["ticker"],
        "direction":  row["direction"],
        "signal":     json.loads(row["signal_json"]),
        "message_id": row["message_id"],
        "status":     row["status"],
        "entry":      row["entry"],
        "exit":       row["exit"],
        "pnl_eur":    row["pnl_eur"],
        "pnl_pct":    row["pnl_pct"],
        "trade_date": row["trade_date"],
        "created_at": row["created_at"] if "created_at" in row.keys() else None,
    }
    keys = set(row.keys())
    for key in ("broker_order_id", "broker_status", "broker_filled_qty",
                "broker_filled_avg_price", "broker_updated_at", "high_water"):
        out[key] = row[key] if key in keys else None
    return out


def has_trade_today(user_id: int, ticker: str) -> bool:
    """True, wenn für diese Aktie heute bereits ein Signal/Trade existiert (egal welcher Status)."""
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT 1 AS present FROM trades WHERE user_id = :user_id "
            "AND trade_date = :trade_date AND ticker = :ticker LIMIT 1",
            {"user_id": user_id, "trade_date": _today(), "ticker": ticker},
        )
    return row is not None


def has_open_position(user_id: int, ticker: str) -> bool:
    """Duplikat-Schutz fürs Senden: heute schon ein Datensatz ODER ein über Nacht offener
    (aktiver) Trade dieser Aktie (egal welches Datum)."""
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT 1 AS present FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND (trade_date = :trade_date OR status = 'active') LIMIT 1",
            {"user_id": user_id, "ticker": ticker, "trade_date": _today()},
        )
    return row is not None


def add_pending(user_id: int, signal: dict, message_id: int) -> bool:
    """Fügt einen vorgemerkten Trade hinzu. Gibt True zurück, wenn neu angelegt;
    False, wenn für diese Aktie heute bereits ein Datensatz existiert (Duplikat-Schutz —
    ein bereits aktiver/abgeschlossener Trade wird NICHT auf 'pending' zurückgesetzt)."""
    signal = _with_strategy_version(signal)   # Gate P5: jedes persistierte Signal referenziert seine Version
    ticker, trade_date = signal["ticker"], _today()
    with _database().transaction() as transaction:
        trade_id = transaction.insert_id(
            """INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json,
                                    message_id, status, created_at)
               VALUES (:user_id, :trade_date, :ticker, :direction, :signal_json, :message_id,
                       'pending', :created_at)
               ON CONFLICT (user_id, trade_date, ticker) DO NOTHING""",
            {"user_id": user_id, "trade_date": trade_date, "ticker": ticker,
             "direction": signal["direction"], "signal_json": json.dumps(signal, default=str),
             "message_id": message_id, "created_at": _utc_timestamp()},
        )
        created = trade_id > 0
        if created:
            _log_event(transaction, trade_id=trade_id, user_id=user_id, ticker=ticker,
                       trade_date=trade_date, from_status=None, to_status="pending")
    if created:
        log.info(f"Trade vorgemerkt: user_id={user_id} {ticker}")
    else:
        log.info(f"Trade übersprungen (heute schon vorhanden): user_id={user_id} {ticker}")
    return created


def activate_trade(user_id: int, ticker: str, status: str = "active") -> dict | None:
    """Aktiviert einen pendenten Trade nach JA-Klick, holt den aktuellen Kurs.

    Standard ist `active` (Demo-Modus). Bei echter Broker-Ausführung kann der Aufrufer
    `status='broker_pending'` setzen; dann wird der Trade erst nach Fill zu `active`.
    """
    if status not in ("active", "broker_pending"):
        raise ValueError(f"Ungültiger Aktivierungsstatus: {status}")
    trade_date = _today()
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT * FROM trades WHERE user_id = :user_id AND trade_date = :trade_date "
            "AND ticker = :ticker",
            {"user_id": user_id, "trade_date": trade_date, "ticker": ticker},
        )
        if not row or row["status"] != "pending":
            return None
        signal = json.loads(row["signal_json"])
    try:
        entry_price = float(yf.Ticker(ticker).fast_info.last_price)
    except Exception:
        entry_price = float(signal["price"])
    with _database().transaction() as transaction:
        changed = transaction.execute(
            "UPDATE trades SET status = :status, entry = :entry WHERE id = :trade_id "
            "AND status = 'pending'",
            {"status": status, "entry": entry_price, "trade_id": row["id"]},
        )
        if changed != 1:
            return None
        _log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status="pending", to_status=status)
    log.info(f"Trade aktiviert: user_id={user_id} {ticker} @ ${entry_price:.2f} ({status})")
    return get_trade(user_id, ticker)


def set_active_entry(user_id: int, ticker: str, entry: float) -> bool:
    """Setzt den Einstiegskurs eines aktiven Trades neu (z. B. Reparatur eines fehlerhaft
    übernommenen Options-Trades, dessen entry die Prämie statt des Underlying-Kurses war)."""
    with _database().transaction() as transaction:
        changed = transaction.execute(
            "UPDATE trades SET entry = :entry WHERE user_id = :user_id AND ticker = :ticker "
            "AND status = 'active'",
            {"entry": float(entry), "user_id": user_id, "ticker": ticker},
        )
        return changed > 0


def heal_absurd_closed_pnl(user_id: int) -> list[dict]:
    """Korrigiert abgeschlossene AKTIEN-Trades mit unplausiblem Einstieg (Glitch-Fill).

    Signatur: geschlossener Trade, dessen `entry` außerhalb 0,5–2,0× des Signalkurses liegt
    (z. B. KHC @ 0,26 statt Signalkurs 23,95 → +9.182 % / +53.810 € Fake-P&L). Setzt
    `entry` = Signalkurs und rechnet pnl_pct/pnl_eur konsistent neu — die Geld-Skala des
    Trades bleibt erhalten (K = pnl_eur/pnl_pct, unabhängig von der aktuellen Trade-Größe).

    Options-/übernommene Trades werden übersprungen (dort ist entry≠Signalkurs legitim).
    Idempotent: nach der Korrektur liegt entry im Band → wird nicht erneut angefasst.
    Gibt die Liste der Korrekturen zurück (für Logging). Gegenstück zum Fill-Guard in
    `mark_broker_filled` (verhindert neue Fälle)."""
    fixed: list[dict] = []
    # Operationally single-run; the plausibility predicate makes repeats idempotent.
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT id, ticker, direction, signal_json, entry, exit, pnl_pct, pnl_eur "
            "FROM trades WHERE user_id = :user_id AND status = 'closed'",
            {"user_id": user_id},
        )
        for r in rows:
            try:
                sig = json.loads(r["signal_json"]) or {}
            except Exception:
                continue
            if sig.get("option_symbol") or sig.get("adopted"):
                continue                              # dort ist entry (Underlying) ≠ Signal (Prämie) ok
            sp, e, ex = sig.get("price"), r["entry"], r["exit"]
            if not sp or not e or float(sp) <= 0 or float(e) <= 0 or ex is None:
                continue
            if 0.5 <= float(e) / float(sp) <= 2.0:
                continue                              # Einstieg plausibel → nichts zu tun
            new_entry = float(sp)
            if r["direction"] == "short":
                new_pct = (new_entry - float(ex)) / new_entry * 100
            else:
                new_pct = (float(ex) - new_entry) / new_entry * 100
            # Geld-Skala (trade_size×Hebel/100) aus dem Altwert ableiten → konsistente Neuberechnung.
            k = (r["pnl_eur"] / r["pnl_pct"]) if r["pnl_pct"] else 0.0
            new_eur = round(k * new_pct, 2)
            transaction.execute(
                "UPDATE trades SET entry = :entry, pnl_pct = :pnl_pct, pnl_eur = :pnl_eur "
                "WHERE id = :trade_id",
                {"entry": round(new_entry, 6), "pnl_pct": round(new_pct, 4),
                 "pnl_eur": new_eur, "trade_id": r["id"]},
            )
            fixed.append({"ticker": r["ticker"], "old_entry": float(e), "new_entry": new_entry,
                          "old_eur": r["pnl_eur"], "new_eur": new_eur})
    if fixed:
        log.warning("[%s] %d absurde(r) geschlossene(r) Trade(s) korrigiert: %s", user_id, len(fixed),
                    ", ".join(f"{x['ticker']} {x['old_eur']:+.0f}€→{x['new_eur']:+.2f}€" for x in fixed))
    return fixed


def mark_broker_pending(user_id: int, ticker: str, *, order_id: str | None, broker_status: str | None) -> bool:
    """Speichert, dass die Broker-Order angenommen, aber noch nicht gefüllt ist."""
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status IN ('broker_pending', 'active') ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            """UPDATE trades
               SET status = 'broker_pending', broker_order_id = :order_id,
                   broker_status = :broker_status,
                   broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = :expected_status""",
            {"order_id": order_id, "broker_status": broker_status, "updated_at": _utc_timestamp(),
             "trade_id": row["id"], "expected_status": row["status"]},
        )
        if changed != 1:
            return False
        _log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status=row["status"],
                   to_status="broker_pending", broker_status=broker_status)
        return True


def mark_broker_filled(user_id: int, ticker: str, *, broker_status: str = "filled",
                       filled_qty: float | None = None, filled_avg_price: float | None = None) -> bool:
    """Macht aus einer broker_pending-Order erst nach tatsächlichem Fill einen aktiven Trade."""
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, entry, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker AND status = 'broker_pending' "
            "ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        # Fill-Preis nur dann als Einstieg übernehmen, wenn er plausibel ist. Ein absurder
        # Broker-Fill (z. B. KHC @ 0,26 statt Signalkurs 23,95) überschrieb sonst den korrekten
        # Einstieg und erzeugte gigantische Fake-P&L (+53.810 €). Außerhalb des 0,5–2,0-Bands
        # um den erwarteten Einstieg (Signalkurs) behalten wir diesen und protokollieren den Fill.
        expected = float(row["entry"] or 0)
        fp = float(filled_avg_price or 0)
        if fp > 0 and (expected <= 0 or 0.5 <= fp / expected <= 2.0):
            entry = fp
        else:
            entry = expected or fp or 0.0
            if fp > 0 and expected > 0:
                log.warning(
                    f"[{user_id}] {ticker}: unplausibler Fill-Preis {fp:g} (erwartet ~{expected:g}) "
                    f"— Signalkurs als Einstieg behalten, um Fake-P&L zu vermeiden."
                )
        changed = transaction.execute(
            """UPDATE trades
               SET status = 'active', entry = :entry, broker_status = :broker_status,
                   broker_filled_qty = :filled_qty,
                   broker_filled_avg_price = :filled_avg_price, broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = 'broker_pending'""",
            {"entry": entry, "broker_status": broker_status, "filled_qty": filled_qty,
             "filled_avg_price": filled_avg_price, "updated_at": _utc_timestamp(),
             "trade_id": row["id"]},
        )
        if changed != 1:
            return False
        _log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status="broker_pending",
                   to_status="active", broker_status=broker_status)
        return True


def mark_broker_failed(user_id: int, ticker: str, *, broker_status: str | None) -> bool:
    """Markiert eine nicht ausgeführte Broker-Order; sie darf nicht als aktiver Trade erscheinen."""
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status IN ('broker_pending', 'active') ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            """UPDATE trades SET status = 'broker_failed', broker_status = :broker_status,
                                 broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = :expected_status""",
            {"broker_status": broker_status, "updated_at": _utc_timestamp(),
             "trade_id": row["id"], "expected_status": row["status"]},
        )
        if changed != 1:
            return False
        _log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status=row["status"],
                   to_status="broker_failed", broker_status=broker_status)
        return True


def mark_broker_closing(user_id: int, ticker: str, *, order_id: str | None, broker_status: str | None) -> bool:
    """Speichert, dass eine Broker-Schließung angestoßen wurde, die Position aber noch offen ist."""
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status IN ('active', 'broker_closing') ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            """UPDATE trades
               SET status = 'broker_closing', broker_order_id = :order_id,
                   broker_status = :broker_status, broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = :expected_status""",
            {"order_id": order_id, "broker_status": broker_status, "updated_at": _utc_timestamp(),
             "trade_id": row["id"], "expected_status": row["status"]},
        )
        if changed != 1:
            return False
        _log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status=row["status"],
                   to_status="broker_closing", broker_status=broker_status)
        return True


def mark_broker_close_failed(user_id: int, ticker: str, *, broker_status: str | None) -> bool:
    """Bringt einen fehlgeschlagenen Sell-Versuch wieder in den aktiven Zustand zurück."""
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status = 'broker_closing' ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            """UPDATE trades SET status = 'active', broker_status = :broker_status,
                                 broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = 'broker_closing'""",
            {"broker_status": broker_status, "updated_at": _utc_timestamp(),
             "trade_id": row["id"]},
        )
        if changed != 1:
            return False
        _log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status="broker_closing",
                   to_status="active", broker_status=broker_status, note="close_failed")
        return True


def adopt_active_trade(user_id: int, ticker: str, *, entry: float, signal: dict,
                       filled_qty: float | None = None,
                       broker_order_id: str | None = None) -> bool:
    """Übernimmt eine **verwaiste Broker-Position** als aktiven Trade (Selbstheilung).

    Gerät eine Order beim Broker durch, ohne dass der Bot sie als aktiven Trade führt
    (z. B. mehrdeutiger Sende-Fehler nach dem eigentlichen Fill), legt diese Funktion den
    Trade nachträglich an, damit der Bot ihn wieder überwacht (SL/TP, Tagesende).

    - Existiert heute schon ein Datensatz dieser Aktie und ist er **nicht-terminal**
      (`active`/`broker_pending`/`broker_closing`) → nichts tun (schon getrackt), `False`.
    - Existiert ein terminaler heutiger Datensatz (z. B. `broker_failed`) → reaktivieren.
    - Sonst → neuen aktiven Trade einfügen.
    Gibt `True` zurück, wenn übernommen wurde."""
    direction = signal.get("direction", "long")
    payload = json.dumps(signal, default=str)
    trade_date = _today()
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status FROM trades WHERE user_id = :user_id AND trade_date = :trade_date "
            "AND ticker = :ticker",
            {"user_id": user_id, "trade_date": trade_date, "ticker": ticker},
        )
        if row and row["status"] in ("active", "broker_pending", "broker_closing"):
            return False
        if row:
            changed = transaction.execute(
                """UPDATE trades
                   SET status = 'active', direction = :direction, entry = :entry,
                       signal_json = :signal_json,
                       exit = NULL, pnl_eur = NULL, pnl_pct = NULL,
                       broker_order_id = :broker_order_id, broker_filled_qty = :filled_qty,
                       broker_status = 'adopted_orphan', broker_updated_at = :updated_at
                   WHERE id = :trade_id AND status = :expected_status""",
                {"direction": direction, "entry": entry, "signal_json": payload,
                 "broker_order_id": broker_order_id, "filled_qty": filled_qty,
                 "updated_at": _utc_timestamp(), "trade_id": row["id"],
                 "expected_status": row["status"]},
            )
            if changed != 1:
                return False
            trade_id, from_status = row["id"], row["status"]
        else:
            trade_id = transaction.insert_id(
                """INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json,
                                       message_id, status, entry, broker_order_id,
                                       broker_filled_qty, broker_status, broker_updated_at,
                                       created_at)
                   VALUES (:user_id, :trade_date, :ticker, :direction, :signal_json, 0,
                           'active', :entry, :broker_order_id, :filled_qty, 'adopted_orphan',
                           :broker_updated_at, :created_at)
                   ON CONFLICT (user_id, trade_date, ticker) DO NOTHING""",
                {"user_id": user_id, "trade_date": trade_date, "ticker": ticker,
                 "direction": direction, "signal_json": payload, "entry": entry,
                 "broker_order_id": broker_order_id, "filled_qty": filled_qty,
                 "broker_updated_at": _utc_timestamp(), "created_at": _utc_timestamp()},
            )
            if not trade_id:
                return False
            from_status = None
        _log_event(transaction, trade_id=trade_id, user_id=user_id, ticker=ticker,
                   trade_date=trade_date, from_status=from_status, to_status="active",
                   broker_status="adopted_orphan", note="adopted_orphan")
    log.warning(f"Verwaiste Broker-Position übernommen: user_id={user_id} {ticker} @ ${entry:.2f}")
    return True


def reject_trade(user_id: int, ticker: str) -> bool:
    """Markiert den heutigen Trade als abgelehnt. Gibt True zurück, falls ein Datensatz aktualisiert wurde."""
    return _terminate_pending(user_id, ticker, "rejected")


def expire_trade(user_id: int, ticker: str) -> bool:
    """Markiert einen noch ausstehenden Trade als abgelaufen (Start-Zeitfenster verstrichen).
    Gibt True zurück, falls ein Datensatz aktualisiert wurde (also noch 'pending' war)."""
    return _terminate_pending(user_id, ticker, "expired")


def expire_stale_pending(cutoff_date: str | None = None) -> int:
    """Läuft ALLE noch ausstehenden (pending) Trades ab, deren Handelstag VOR `cutoff_date` liegt
    (Default: heute). Räumt liegengebliebene Signale auf, die nie angenommen wurden (z. B.
    Auto-Accept außerhalb der Sitzung) — `get_pending_trades` filtert ohnehin auf `trade_date=heute`,
    ältere blieben sonst als Karteileichen liegen (beobachteter Pending-Stau). Setzt Status
    'expired' + Event je Datensatz; gibt die Anzahl bereinigter Trades zurück."""
    cutoff = cutoff_date or _today()
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT id, user_id, ticker, trade_date FROM trades "
            "WHERE status = 'pending' AND trade_date < :cutoff",
            {"cutoff": cutoff},
        )
        changed_count = 0
        for r in rows:
            changed = transaction.execute(
                "UPDATE trades SET status = 'expired' WHERE id = :trade_id AND status = 'pending'",
                {"trade_id": r["id"]},
            )
            if changed != 1:
                continue
            changed_count += 1
            _log_event(transaction, trade_id=r["id"], user_id=r["user_id"], ticker=r["ticker"],
                       trade_date=r["trade_date"], from_status="pending", to_status="expired")
    if changed_count:
        log.info(f"Stale-Pending bereinigt: {changed_count} liegengebliebene Signale abgelaufen (< {cutoff}).")
    return changed_count


def _terminate_pending(user_id: int, ticker: str, to_status: str) -> bool:
    """Setzt den heutigen pendenten Trade auf einen Endstatus (rejected/expired) + Event."""
    trade_date = _today()
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT id FROM trades WHERE user_id = :user_id AND trade_date = :trade_date "
            "AND ticker = :ticker AND status = 'pending'",
            {"user_id": user_id, "trade_date": trade_date, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            "UPDATE trades SET status = :to_status WHERE id = :trade_id AND status = 'pending'",
            {"to_status": to_status, "trade_id": row["id"]},
        )
        if changed != 1:
            return False
        _log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=trade_date, from_status="pending", to_status=to_status)
        return True


def get_active_trades(user_id: int) -> list[dict]:
    """Gibt ALLE aktiven Trades des Nutzers zurück (auch über Nacht gehaltene, datumsunabhängig)."""
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND status = 'active' "
            "ORDER BY trade_date ASC, id ASC", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_pending_trades(user_id: int) -> list[dict]:
    """Gibt alle heute noch ausstehenden (pending) Trades des Nutzers zurück."""
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND trade_date = :trade_date "
            "AND status = 'pending' ORDER BY id ASC",
            {"user_id": user_id, "trade_date": _today()},
        )
    return [_trade_to_dict(r) for r in rows]


def get_broker_pending_trades(user_id: int) -> list[dict]:
    """Broker-Orders, die angenommen, aber noch nicht tatsächlich gefüllt wurden."""
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND status = 'broker_pending' "
            "ORDER BY trade_date ASC, id ASC", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_broker_closing_trades(user_id: int) -> list[dict]:
    """Broker-Schließungen, die angestoßen wurden, aber noch nicht final bestätigt sind."""
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND status = 'broker_closing' "
            "ORDER BY trade_date ASC, id ASC", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_trade(user_id: int, ticker: str) -> dict | None:
    """Relevantester Trade einer Aktie: aktiver (über Nacht gehaltener) zuerst, sonst der heutige.
    So funktioniert Verkaufen/Hebel auch bei datumsübergreifend offenen Trades."""
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT * FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND (status = 'active' OR trade_date = :trade_date) "
            "ORDER BY (status = 'active') DESC, trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker, "trade_date": _today()},
        )
    return _trade_to_dict(row) if row else None


def get_trade_by_id(trade_id: int) -> dict | None:
    """Liefert einen Trade anhand seiner global eindeutigen ID (OMS-Signal-Bridge)."""
    with _database().transaction() as transaction:
        row = transaction.one("SELECT * FROM trades WHERE id = :trade_id", {"trade_id": trade_id})
    return _trade_to_dict(row) if row else None


def close_all(user_id: int, results: list[dict], *, broker_status: str | None = None):
    """Schließt die ausgewerteten Trades des Nutzers (matcht den AKTIVEN Trade je Aktie,
    datumsunabhängig — auch über Nacht gehaltene).

    Optional kann ein `broker_status` mitgeschrieben werden, z. B. für Reconcile-Fälle.
    """
    with _database().transaction() as transaction:
        for r in results:
            row = transaction.one(
                "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id "
                "AND ticker = :ticker "
                "AND status IN ('active', 'broker_closing') ORDER BY trade_date DESC, id DESC LIMIT 1",
                {"user_id": user_id, "ticker": r["ticker"]},
            )
            if not row:
                continue
            changed = transaction.execute(
                """UPDATE trades SET status = 'closed', exit = :exit, pnl_eur = :pnl_eur,
                                      pnl_pct = :pnl_pct,
                                      broker_status = COALESCE(:broker_status, broker_status),
                                      broker_updated_at = CASE WHEN :broker_status IS NOT NULL
                                          THEN :updated_at ELSE broker_updated_at END
                   WHERE id = :trade_id AND status = :expected_status""",
                {"exit": r["exit"], "pnl_eur": r["pnl_eur"], "pnl_pct": r["pnl_pct"],
                 "broker_status": broker_status, "updated_at": _utc_timestamp(),
                 "trade_id": row["id"], "expected_status": row["status"]},
            )
            if changed != 1:
                continue
            _log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=r["ticker"],
                       trade_date=row["trade_date"], from_status=row["status"],
                       to_status="closed", broker_status=broker_status)
    log.info(f"user_id={user_id}: {len(results)} Trades geschlossen.")


def get_history(user_id: int, days: int = 30) -> list[dict]:
    """Gibt die abgeschlossenen Trades der letzten N Tage zurück (neueste zuerst)."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=int(days))).isoformat()
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trades WHERE user_id = :user_id AND status = 'closed' "
            "AND trade_date >= :cutoff ORDER BY trade_date DESC, id DESC",
            {"user_id": user_id, "cutoff": cutoff},
        )
    return [_trade_to_dict(r) for r in rows]


def get_closed_trades(user_id: int) -> list[dict]:
    """Alle abgeschlossenen Trades des Nutzers, älteste zuerst (für Equity-Kurve & Statistik)."""
    with _database().transaction() as transaction:
        rows = transaction.all(
            """SELECT * FROM trades
               WHERE user_id = :user_id AND status = 'closed'
               ORDER BY trade_date ASC, id ASC""", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_realized_pnl_today(user_id: int) -> float:
    """Summe des heute REALISIERTEN P&L eines Nutzers (geschlossene Trades des UTC-Handelstags).

    Grundlage für das Tagesverlustlimit (RISK-004, ``pretrade_check`` Schritt 10): nur
    ``status = 'closed'``-Trades des heutigen ``trade_date`` (UTC, `today_utc()`) mit
    gesetztem ``pnl_eur`` zählen. Ohne solche Trades ist der realisierte Tages-P&L 0.0.
    Unrealisierte P&L offener Positionen fließen bewusst NICHT ein.
    """
    with _database().transaction() as transaction:
        row = transaction.one(
            "SELECT COALESCE(SUM(pnl_eur), 0.0) AS realized FROM trades "
            "WHERE user_id = :user_id AND status = 'closed' AND trade_date = :trade_date "
            "AND pnl_eur IS NOT NULL",
            {"user_id": user_id, "trade_date": today_utc()},
        )
    return float(row["realized"]) if row and row["realized"] is not None else 0.0


def get_all_trades(user_id: int) -> list[dict]:
    """Alle Trades des Nutzers für Export/Analyse, älteste zuerst und statusübergreifend."""
    with _database().transaction() as transaction:
        rows = transaction.all(
            """SELECT * FROM trades
               WHERE user_id = :user_id
               ORDER BY trade_date ASC, id ASC""", {"user_id": user_id}
        )
    return [_trade_to_dict(r) for r in rows]


def get_all_trades_between(user_id: int, date_from: str | None = None,
                           date_to: str | None = None) -> list[dict]:
    """Trades des Nutzers, optional auf `trade_date ∈ [date_from, date_to]` (inklusiv) gefiltert.
    Datumsformat 'YYYY-MM-DD'; None = keine Grenze."""
    sql = "SELECT * FROM trades WHERE user_id = :user_id"
    params = {"user_id": user_id}
    if date_from:
        sql += " AND trade_date >= :date_from"; params["date_from"] = date_from
    if date_to:
        sql += " AND trade_date <= :date_to"; params["date_to"] = date_to
    sql += " ORDER BY trade_date ASC, id ASC"
    with _database().transaction() as transaction:
        rows = transaction.all(sql, params)
    return [_trade_to_dict(r) for r in rows]


def get_trade_events(trade_id: int) -> list[dict]:
    """Alle Status-Events eines Trades, chronologisch (für Dauer-Berechnung)."""
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trade_events WHERE trade_id = :trade_id ORDER BY ts ASC, id ASC",
            {"trade_id": trade_id},
        )
    return [dict(r) for r in rows]


def get_events_by_trade(user_id: int) -> dict[int, list[dict]]:
    """Alle Events des Nutzers, gruppiert nach trade_id (ein Query statt N — für den Export)."""
    out: dict[int, list[dict]] = {}
    with _database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM trade_events WHERE user_id = :user_id ORDER BY ts ASC, id ASC",
            {"user_id": user_id},
        )
    for r in rows:
        out.setdefault(r["trade_id"], []).append(dict(r))
    return out


def get_trade_events_between(user_id: int, ts_from: str | None = None,
                             ts_to: str | None = None) -> list[dict]:
    """Status-Events des Nutzers im Zeitfenster [ts_from, ts_to] (inklusiv), chronologisch.
    `ts`-Format 'YYYY-MM-DD HH:MM:SS' (UTC); ein reines Datum filtert ab/bis Tagesgrenze."""
    sql = "SELECT * FROM trade_events WHERE user_id = :user_id"
    params = {"user_id": user_id}
    if ts_from:
        sql += " AND ts >= :ts_from"; params["ts_from"] = ts_from
    if ts_to:
        sql += " AND ts <= :ts_to"
        params["ts_to"] = ts_to + " 23:59:59" if len(ts_to) == 10 else ts_to
    sql += " ORDER BY ts ASC, id ASC"
    with _database().transaction() as transaction:
        rows = transaction.all(sql, params)
    return [dict(r) for r in rows]


# ── Intraday-Ticks (Kurs- & Stärke-Verlauf je aktivem Trade) ────────────────

def add_tick(user_id: int, ticker: str, price: float | None, strength: float | None):
    """Schreibt einen Verlaufspunkt (Kurs + Signal-Stärke) für einen aktiven Trade."""
    with _database().transaction() as transaction:
        transaction.execute(
            "INSERT INTO trade_ticks (user_id, trade_date, ticker, ts, price, strength) "
            "VALUES (:user_id, :trade_date, :ticker, :ts, :price, :strength)",
            {"user_id": user_id, "trade_date": _today(), "ticker": ticker,
             "ts": _utc_timestamp(), "price": price, "strength": strength},
        )


def update_high_water(user_id: int, ticker: str, price: float | None) -> float | None:
    """Schreibt den Hoechstkurs seit Einstieg fort und gibt den gueltigen Wert zurueck.

    Monoton steigend ueber die WHERE-Bedingung statt einer skalaren Funktion: SQLite kennt
    `MAX(a, b)`, Postgres nur `GREATEST` — die Bedingung laeuft auf beiden Backends gleich
    und kann den Wert nicht zurueckdrehen. Ein leerer Ausgangswert wird mit dem aktuellen
    Kurs initialisiert — der Trailing-Stop liegt dann eine ATR-Spanne darunter und kann
    nicht verfrueht ausloesen.
    Betrifft nur den aktiven Trade des Tages-Schluessels (user_id, ticker, status='active')."""
    if price is None:
        return None
    with _database().transaction() as transaction:
        transaction.execute(
            """UPDATE trades SET high_water = :price
               WHERE user_id = :user_id AND ticker = :ticker AND status = 'active'
                 AND (high_water IS NULL OR high_water < :price)""",
            {"user_id": user_id, "ticker": ticker, "price": float(price)},
        )
        row = transaction.one(
            """SELECT high_water FROM trades
               WHERE user_id = :user_id AND ticker = :ticker AND status = 'active'""",
            {"user_id": user_id, "ticker": ticker},
        )
    return float(row["high_water"]) if row and row["high_water"] is not None else None


def get_today_ticks(user_id: int) -> dict:
    """Gibt die heutigen Verlaufspunkte je Ticker zurück: { ticker: [{ts, price, strength}, ...] }."""
    with _database().transaction() as transaction:
        rows = transaction.all(
            """SELECT ticker, ts, price, strength FROM trade_ticks
               WHERE user_id = :user_id AND trade_date = :trade_date
               ORDER BY ts ASC, id ASC""",
            {"user_id": user_id, "trade_date": _today()},
        )
    series: dict[str, list] = {}
    for r in rows:
        series.setdefault(r["ticker"], []).append(
            {"ts": r["ts"], "price": r["price"], "strength": r["strength"]}
        )
    return series


from . import strategies   # init_db setzt den Strategieversions-Cache zurück

# Strategie-Konfigurationen und -Versionen.
# `_STRATEGY_VERSIONS_BOOTSTRAPPED` ist ein modulinterner Cache-Schalter; maßgeblich ist
# immer `strategies._STRATEGY_VERSIONS_BOOTSTRAPPED`, der Wert hier ist nur eine Kopie.
from .strategies import (                                                      # noqa: E402
    _strategy_config_to_dict, list_strategy_configs, get_strategy_config,
    upsert_strategy_config, search_strategy_configs, _STRATEGY_VERSION_CACHE,
    _STRATEGY_VERSIONS_BOOTSTRAPPED, _current_code_commit, _strategy_content_hash,
    publish_strategy_version, get_strategy_version, _latest_strategy_version_id,
    ensure_strategy_versions_published, resolve_strategy_version_id, _with_strategy_version,
)

# Rohdatenarchiv, Shadow-Snapshots, Polymarket
from .research import (                                                        # noqa: E402
    record_raw_data_archive_entry, list_raw_data_archive_entries, record_shadow_snapshot,
    get_shadow_snapshots, _polymarket_timestamp, upsert_polymarket_market,
    record_polymarket_snapshot, polymarket_snapshot_history, list_polymarket_markets,
    list_polymarket_snapshots,
)


# OMS-Orders, Order-Events, Schutzorders
from .orders import (                                                          # noqa: E402
    get_order_by_idempotency_key, get_oms_order, get_open_oms_orders,
    get_active_protective_orders, record_protective_order, get_oms_trade_intent,
    create_oms_order, transition_oms_order, record_oms_order_event, get_oms_order_events,
    burn_in_order_stats,
)

# Audit-Log, Kill-Switch, Risikoprofile
from .safety import (                                                          # noqa: E402
    _audit_timestamp, append_audit_event, _as_audit_event, audit_events_for_entity,
    all_audit_events, _kill_switch_timestamp, activate_kill_switch, deactivate_kill_switch,
    get_active_kill_switches, get_post_trade_risk_rows, get_risk_profile, save_risk_profile,
)

# Outbox und Callback-Tokens
from .messaging import (                                                       # noqa: E402
    enqueue_outbox_event, fetch_due_outbox_events, mark_outbox_delivered, mark_outbox_retry,
    mark_outbox_dead, outbox_backlog_count, issue_callback_token, resolve_callback_token,
    purge_expired_callback_tokens,
)


# Nutzer, Profil, Einstellungen, Zugangsdaten, Benachrichtigungen
from .users import (                                                           # noqa: E402
    _update_user, _mutate_user_text, _user_to_dict, _parse_strategies, _parse_watchlist,
    _parse_regions, get_or_create_user, get_user, save_profile, get_decrypted_credentials,
    store_broker_oauth_connection, get_broker_oauth_connection,
    disconnect_broker_oauth_connection, revoke_broker_oauth_connection, list_active_users,
    set_user_active, set_market_region, toggle_region, set_trade_size, set_top_n,
    set_sl_tp_mode, set_leverage, set_auto_accept, set_auto_universe, set_strategy,
    set_llm_rank, set_eod_close, set_signal_window, set_broker_exec, set_alpaca_credentials,
    clear_alpaca_credentials, has_alpaca_credentials, toggle_strategy, add_watchlist_tickers,
    remove_watchlist_ticker, set_notify_channel, set_asset_pref, add_notification,
    get_notifications, unread_count, mark_notifications_read,
)

# Dashboard-Token und Web-Sessions
from .sessions import (                                                        # noqa: E402
    get_or_create_dashboard_token, rotate_dashboard_token, get_user_by_token, _hash_token,
    _is_token_hash, create_session, user_id_for_session, delete_session,
    delete_user_sessions, delete_expired_sessions,
)
