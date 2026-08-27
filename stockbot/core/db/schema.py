"""Tabellen und Alt-Migrationen.

``SCHEMA_SQL`` ist der maschinenlesbare Vertrag für die SQLite-Laufzeit. Unter PostgreSQL
gehört das Schema ausschließlich Alembic — ``init_db()`` legt dort nichts an, sondern
prüft nur lesend, ob alle erwarteten Tabellen schon da sind. Die ``_migrate*``-Funktionen
sind additiv und idempotent: sie heilen bestehende Datenbanken bei jedem Start.
"""

import json
import sqlite3
from contextlib import nullcontext
from stockbot import config
from stockbot.core import db_backend

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db
from . import strategies


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
    db.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with db._connect() as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)


_EXPECTED_POSTGRES_TABLES = frozenset({
    "users", "trades", "trade_ticks", "trades_archive", "trade_ticks_archive",
    "sessions", "notifications", "strategy_configs", "trade_events", "trade_intents",
    "orders", "order_events", "audit_events", "kill_switches",
})


def _check_postgres_schema_readiness() -> None:
    """Read-only startup guard; PostgreSQL schema ownership stays exclusively with Alembic."""
    with db._database().transaction() as transaction:
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
        db.log.info("Migration: Spalte users.dashboard_token ergänzt.")
    if "market_region" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN market_region TEXT NOT NULL DEFAULT 'sp500'")
        db.log.info("Migration: Spalte users.market_region ergänzt.")
    if "top_n_signals" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN top_n_signals INTEGER NOT NULL DEFAULT 5")
        db.log.info("Migration: Spalte users.top_n_signals ergänzt.")
    if "sl_tp_mode" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN sl_tp_mode TEXT NOT NULL DEFAULT 'normal'")
        db.log.info("Migration: Spalte users.sl_tp_mode ergänzt.")
    if "leverage" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN leverage REAL NOT NULL DEFAULT 1.0")
        db.log.info("Migration: Spalte users.leverage ergänzt.")
    if "auto_accept" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN auto_accept INTEGER NOT NULL DEFAULT 0")
        db.log.info("Migration: Spalte users.auto_accept ergänzt.")
    if "auto_universe" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN auto_universe INTEGER NOT NULL DEFAULT 1")
        db.log.info("Migration: Spalte users.auto_universe ergänzt.")
    if "strategy" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN strategy TEXT NOT NULL DEFAULT 'standard'")
        db.log.info("Migration: Spalte users.strategy ergänzt.")
    if "llm_rank" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN llm_rank INTEGER NOT NULL DEFAULT 1")
        db.log.info("Migration: Spalte users.llm_rank ergänzt.")
    if "eod_close" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN eod_close INTEGER NOT NULL DEFAULT 1")
        db.log.info("Migration: Spalte users.eod_close ergänzt.")
    if "broker_exec" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN broker_exec INTEGER NOT NULL DEFAULT 0")
        db.log.info("Migration: Spalte users.broker_exec ergänzt.")
    if "watchlist" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN watchlist TEXT NOT NULL DEFAULT ''")
        db.log.info("Migration: Spalte users.watchlist ergänzt.")
    if "notify_channel" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN notify_channel TEXT NOT NULL DEFAULT 'both'")
        db.log.info("Migration: Spalte users.notify_channel ergänzt.")
    if "asset_pref" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN asset_pref TEXT NOT NULL DEFAULT 'stocks'")
        db.log.info("Migration: Spalte users.asset_pref ergänzt.")
    if "signal_window" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN signal_window INTEGER NOT NULL DEFAULT 0")
        db.log.info("Migration: Spalte users.signal_window ergänzt.")

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
        db.log.info("Migration: Tabelle strategy_configs ergänzt.")
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
                db.log.info(f"Migration: Spalte strategy_configs.{name} ergänzt.")
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
            db.log.info(f"Migration: Spalte trades.{name} ergänzt.")

    # Die Archivtabelle spiegelt `trades` spaltengleich (Reihenfolge inklusive) — die
    # Archivierungs-Query listet die Spalten einzeln auf und bricht sonst.
    archive_cols = {row["name"] for row in conn.execute("PRAGMA table_info(trades_archive)").fetchall()}
    if archive_cols and "high_water" not in archive_cols:
        conn.execute("ALTER TABLE trades_archive ADD COLUMN high_water REAL")
        db.log.info("Migration: Spalte trades_archive.high_water ergänzt.")

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
            db.log.info(f"Migration: trade_events backfilled für {n} Trade(s).")

    # Web-Session-Tokens nur noch als SHA-256-Hash speichern: Klartext-Alttokens einmalig
    # hashen (erkennbar an der Form — token_urlsafe(32) ist 43 Zeichen Base64, kein 64er-Hex).
    legacy = [r["token"] for r in conn.execute("SELECT token FROM sessions").fetchall()
              if not db._is_token_hash(r["token"])]
    for tok in legacy:
        conn.execute("UPDATE sessions SET token = ? WHERE token = ?", (db._hash_token(tok), tok))
    if legacy:
        db.log.info(f"Migration: {len(legacy)} Web-Session-Token(s) gehasht.")

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
        manager = db._database().transaction()
    with manager as transaction:
        n_users = transaction.execute(
            "UPDATE users SET leverage = :cap WHERE leverage > :cap", {"cap": db.MAX_LEVERAGE}
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
                if val is not None and float(val) > db.MAX_LEVERAGE:
                    sig[key] = db.MAX_LEVERAGE
                    changed = True
            if changed:
                transaction.execute(
                    "UPDATE trades SET signal_json = :signal_json WHERE id = :trade_id",
                    {"signal_json": json.dumps(sig), "trade_id": row["id"]},
                )
                n_trades += 1
    if n_users:
        db.log.info(f"Migration: {n_users} users.leverage-Altwert(e) > {db.MAX_LEVERAGE:g}x auf {db.MAX_LEVERAGE:g}x geklemmt.")
    if n_trades:
        db.log.info(f"Migration: {n_trades} offene(r) Trade(s) mit Hebel-Altwert "
                 f"> {db.MAX_LEVERAGE:g}x auf {db.MAX_LEVERAGE:g}x geklemmt.")
