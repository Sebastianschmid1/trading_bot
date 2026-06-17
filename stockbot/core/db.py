"""
SQLite-Persistenz für Multi-User-Betrieb
Ersetzt tracker.py — speichert Nutzerprofile (inkl. verschlüsselter
Broker-Zugangsdaten) und Demo-Trades, jeweils pro user_id (== Telegram chat_id).
"""

import sqlite3
import json
import logging
import secrets
from contextlib import contextmanager
from datetime import date

from cryptography.fernet import Fernet
import yfinance as yf

from stockbot.config import ENCRYPTION_KEY
from stockbot.paths import DATA_DIR

log = logging.getLogger(__name__)
DB_FILE = DATA_DIR / "bot.db"

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
"""


def init_db():
    """Legt data/-Ordner und Tabellen an (idempotent). Beim Start einmal aufrufen."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection):
    """Additive Schema-Migrationen für bestehende Datenbanken (idempotent)."""
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
    }.items():
        if name not in trade_cols:
            conn.execute(ddl)
            log.info(f"Migration: Spalte trades.{name} ergänzt.")


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


def _today() -> str:
    return str(date.today())


# ── Verschlüsselung ─────────────────────────────────────────────────────────

def encrypt(plaintext: str) -> bytes:
    """Verschlüsselt einen String zur Speicherung (z. B. Broker-API-Key/-Secret)."""
    return _fernet.encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Entschlüsselt aus der DB gelesene Bytes zurück zum ursprünglichen String."""
    return _fernet.decrypt(bytes(ciphertext)).decode("utf-8")


# ── User-Profile ────────────────────────────────────────────────────────────

def _user_to_dict(row: sqlite3.Row) -> dict:
    return {
        "user_id":          row["user_id"],
        "username":         row["username"],
        "trade_size_eur":   row["trade_size_eur"],
        "broker_platform":  row["broker_platform"],
        "onboarding_state": row["onboarding_state"],
        "is_active":        bool(row["is_active"]),
        "market_region":    _parse_regions(row["market_region"])[0],   # primärer Bereich (Rückwärtskompat.)
        "market_regions":   _parse_regions(row["market_region"]),      # alle gewählten Körbe
        "top_n_signals":    row["top_n_signals"],
        "sl_tp_mode":       row["sl_tp_mode"],
        "leverage":         row["leverage"],
        "auto_accept":      bool(row["auto_accept"]),
        "auto_universe":    bool(row["auto_universe"]),
        "strategy":         row["strategy"],
        "strategies":       _parse_strategies(row["strategy"]),
        "llm_rank":         bool(row["llm_rank"]),
        "eod_close":        bool(row["eod_close"]),
        "broker_exec":      bool(row["broker_exec"]),
        "signal_window":    bool(row["signal_window"] if "signal_window" in row.keys() else 0),
        "watchlist":        _parse_watchlist(row["watchlist"] if "watchlist" in row.keys() else ""),
        "notify_channel":   (row["notify_channel"] if "notify_channel" in row.keys() else "both") or "both",
        "asset_pref":       (row["asset_pref"] if "asset_pref" in row.keys() else "stocks") or "stocks",
    }


def _parse_strategies(raw: str | None) -> list[str]:
    """Kommagetrennte Strategie-Liste aus der DB → Liste (mind. ein Eintrag)."""
    keys = [s.strip() for s in (raw or "").split(",") if s.strip()]
    return keys or ["standard"]


def _parse_watchlist(raw: str | None) -> list[str]:
    """Kommagetrennte persönliche Watchlist aus der DB → Liste (kann leer sein)."""
    return [s.strip().upper() for s in (raw or "").split(",") if s.strip()]


def _parse_regions(raw: str | None) -> list[str]:
    """Kommagetrennte Markt-Bereich-Liste aus der DB → Liste (mind. ein Eintrag)."""
    keys = [s.strip() for s in (raw or "").split(",") if s.strip()]
    return keys or ["sp500"]


def get_or_create_user(user_id: int, username: str | None = None) -> dict:
    """Holt das Nutzerprofil, legt bei Bedarf einen neuen 'in_progress'-Datensatz an."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (user_id, username, onboarding_state) VALUES (?, ?, 'in_progress')",
                (user_id, username),
            )
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return _user_to_dict(row)


def get_user(user_id: int) -> dict | None:
    """Gibt das Nutzerprofil zurück oder None, falls nicht registriert."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return _user_to_dict(row) if row else None


def save_profile(user_id: int, *, trade_size_eur: float,
                 broker_platform: str | None = None,
                 broker_api_key: str | None = None,
                 broker_api_secret: str | None = None):
    """Speichert das fertige Profil (verschlüsselt Broker-Zugangsdaten) und markiert es als 'complete'."""
    key_enc    = encrypt(broker_api_key) if broker_api_key else None
    secret_enc = encrypt(broker_api_secret) if broker_api_secret else None

    with _connect() as conn:
        conn.execute(
            """UPDATE users
               SET trade_size_eur = ?, broker_platform = ?, broker_api_key = ?,
                   broker_api_secret = ?, onboarding_state = 'complete',
                   updated_at = datetime('now')
               WHERE user_id = ?""",
            (trade_size_eur, broker_platform, key_enc, secret_enc, user_id),
        )
    log.info(f"Profil gespeichert: user_id={user_id} (Broker: {broker_platform or '—'})")


def get_decrypted_credentials(user_id: int) -> tuple[str, str] | None:
    """Gibt (api_key, api_secret) entschlüsselt zurück, oder None falls kein Broker hinterlegt ist."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT broker_api_key, broker_api_secret FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row or row["broker_api_key"] is None or row["broker_api_secret"] is None:
        return None
    return decrypt(row["broker_api_key"]), decrypt(row["broker_api_secret"])


def list_active_users() -> list[dict]:
    """Gibt alle aktiven, vollständig eingerichteten Nutzer zurück (Empfänger der täglichen Jobs)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE is_active = 1 AND onboarding_state = 'complete'"
        ).fetchall()
    return [_user_to_dict(r) for r in rows]


def set_user_active(user_id: int, active: bool):
    """Aktiviert/deaktiviert einen Nutzer (z. B. für ein künftiges /stop)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET is_active = ?, updated_at = datetime('now') WHERE user_id = ?",
            (1 if active else 0, user_id),
        )


def set_market_region(user_id: int, region: str):
    """Setzt den Markt-Bereich des Nutzers auf genau einen Korb (ersetzt die Auswahl)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET market_region = ?, updated_at = datetime('now') WHERE user_id = ?",
            (region, user_id),
        )


def toggle_region(user_id: int, key: str) -> list[str]:
    """Schaltet einen Markt-Korb in der Auswahl des Nutzers an/aus (mind. einer bleibt aktiv).
    Gibt die neue Liste zurück."""
    with _connect() as conn:
        row = conn.execute("SELECT market_region FROM users WHERE user_id = ?", (user_id,)).fetchone()
        keys = _parse_regions(row["market_region"] if row else None)
        if key in keys:
            if len(keys) > 1:          # der letzte Korb bleibt erhalten
                keys.remove(key)
        else:
            keys.append(key)
        conn.execute(
            "UPDATE users SET market_region = ?, updated_at = datetime('now') WHERE user_id = ?",
            (",".join(keys), user_id),
        )
    return keys


def set_trade_size(user_id: int, eur: float) -> float:
    """Setzt die Demo-Trade-Größe in € (auf 1..1.000.000 begrenzt). Gibt den gespeicherten Wert zurück."""
    eur = max(1.0, min(1_000_000.0, float(eur)))
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET trade_size_eur = ?, updated_at = datetime('now') WHERE user_id = ?",
            (eur, user_id),
        )
    return eur


def set_top_n(user_id: int, n: int):
    """Setzt die gewünschte Anzahl täglicher Signale (auf 1..20 begrenzt)."""
    n = max(1, min(20, int(n)))
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET top_n_signals = ?, updated_at = datetime('now') WHERE user_id = ?",
            (n, user_id),
        )


def set_sl_tp_mode(user_id: int, mode: str):
    """Setzt den SL/TP-Modus des Nutzers ('aus' | 'passiv' | 'normal' | 'aggressiv')."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET sl_tp_mode = ?, updated_at = datetime('now') WHERE user_id = ?",
            (mode, user_id),
        )


def set_leverage(user_id: int, leverage: float):
    """Setzt den Standard-Hebel des Nutzers (auf 1..20 begrenzt)."""
    leverage = max(1.0, min(20.0, float(leverage)))
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET leverage = ?, updated_at = datetime('now') WHERE user_id = ?",
            (leverage, user_id),
        )


def set_auto_accept(user_id: int, on: bool):
    """Aktiviert/deaktiviert das automatische Annehmen neuer Signale."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET auto_accept = ?, updated_at = datetime('now') WHERE user_id = ?",
            (1 if on else 0, user_id),
        )


def set_auto_universe(user_id: int, on: bool):
    """Schaltet das Voll-Universum (automatisch geladene Vollliste) an/aus.
    Aus → es wird der kuratierte Korb aus config.py genutzt (schnellere Analyse)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET auto_universe = ?, updated_at = datetime('now') WHERE user_id = ?",
            (1 if on else 0, user_id),
        )


def set_strategy(user_id: int, strategy: str):
    """Setzt die aktiven Signal-Strategien des Nutzers (ein Schlüssel oder kommagetrennte Liste)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET strategy = ?, updated_at = datetime('now') WHERE user_id = ?",
            (strategy, user_id),
        )


def set_llm_rank(user_id: int, on: bool):
    """Aktiviert/deaktiviert das LLM-Ranking (Claude Haiku) für den Nutzer."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET llm_rank = ?, updated_at = datetime('now') WHERE user_id = ?",
            (1 if on else 0, user_id),
        )


def set_eod_close(user_id: int, on: bool):
    """Tagesende-Schließung an/aus. Aus → Trades über Nacht halten (nur SL/TP schließt)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET eod_close = ?, updated_at = datetime('now') WHERE user_id = ?",
            (1 if on else 0, user_id),
        )


def set_signal_window(user_id: int, on: bool):
    """15-Minuten-Annahmefenster an/aus. Aus (Default) → Signale bleiben den ganzen Handelstag annehmbar."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET signal_window = ?, updated_at = datetime('now') WHERE user_id = ?",
            (1 if on else 0, user_id),
        )


def set_broker_exec(user_id: int, on: bool):
    """Echte (Paper-)Order-Ausführung über Alpaca an/aus (Default aus = nur Demo)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET broker_exec = ?, updated_at = datetime('now') WHERE user_id = ?",
            (1 if on else 0, user_id),
        )


def set_alpaca_credentials(user_id: int, api_key: str, api_secret: str):
    """Speichert die Alpaca-API-Zugangsdaten des Nutzers verschlüsselt (broker_platform='alpaca')."""
    with _connect() as conn:
        conn.execute(
            """UPDATE users SET broker_platform = 'alpaca', broker_api_key = ?,
                   broker_api_secret = ?, updated_at = datetime('now') WHERE user_id = ?""",
            (encrypt(api_key), encrypt(api_secret), user_id),
        )
    log.info(f"Alpaca-Zugangsdaten gesetzt: user_id={user_id}")


def clear_alpaca_credentials(user_id: int):
    """Entfernt die Alpaca-Zugangsdaten und schaltet die Broker-Ausführung ab."""
    with _connect() as conn:
        conn.execute(
            """UPDATE users SET broker_platform = NULL, broker_api_key = NULL,
                   broker_api_secret = NULL, broker_exec = 0,
                   updated_at = datetime('now') WHERE user_id = ?""",
            (user_id,),
        )
    log.info(f"Alpaca-Zugangsdaten entfernt: user_id={user_id}")


def has_alpaca_credentials(user_id: int) -> bool:
    """True, wenn der Nutzer eigene Alpaca-Zugangsdaten hinterlegt hat."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT broker_platform, broker_api_key FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return bool(row and row["broker_platform"] == "alpaca" and row["broker_api_key"] is not None)


def toggle_strategy(user_id: int, key: str) -> list[str]:
    """Schaltet eine Strategie in der Auswahl des Nutzers an/aus (mind. eine bleibt aktiv).
    Gibt die neue Liste zurück."""
    with _connect() as conn:
        row = conn.execute("SELECT strategy FROM users WHERE user_id = ?", (user_id,)).fetchone()
        keys = _parse_strategies(row["strategy"] if row else None)
        if key in keys:
            if len(keys) > 1:          # die letzte Strategie nicht abschaltbar
                keys.remove(key)
        else:
            keys.append(key)
        conn.execute(
            "UPDATE users SET strategy = ?, updated_at = datetime('now') WHERE user_id = ?",
            (",".join(keys), user_id),
        )
    return keys


def add_watchlist_tickers(user_id: int, tickers: list[str]) -> list[str]:
    """Fügt Symbole zur persönlichen Watchlist hinzu (dedupliziert, großgeschrieben, sortiert).
    Gibt die neue Liste zurück."""
    with _connect() as conn:
        row = conn.execute("SELECT watchlist FROM users WHERE user_id = ?", (user_id,)).fetchone()
        current = set(_parse_watchlist(row["watchlist"] if row else None))
        current.update(t.strip().upper() for t in tickers if t.strip())
        new_list = sorted(current)
        conn.execute(
            "UPDATE users SET watchlist = ?, updated_at = datetime('now') WHERE user_id = ?",
            (",".join(new_list), user_id),
        )
    return new_list


def remove_watchlist_ticker(user_id: int, ticker: str) -> list[str]:
    """Entfernt ein Symbol aus der persönlichen Watchlist. Gibt die neue Liste zurück."""
    target = ticker.strip().upper()
    with _connect() as conn:
        row = conn.execute("SELECT watchlist FROM users WHERE user_id = ?", (user_id,)).fetchone()
        new_list = [t for t in _parse_watchlist(row["watchlist"] if row else None) if t != target]
        conn.execute(
            "UPDATE users SET watchlist = ?, updated_at = datetime('now') WHERE user_id = ?",
            (",".join(new_list), user_id),
        )
    return new_list


def set_trade_leverage(user_id: int, ticker: str, leverage: float):
    """Ändert den Hebel eines noch ausstehenden Trades (im gespeicherten signal_json)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT signal_json FROM trades WHERE user_id = ? AND trade_date = ? AND ticker = ?",
            (user_id, _today(), ticker),
        ).fetchone()
        if not row:
            return
        sig = json.loads(row["signal_json"])
        sig["leverage"] = float(leverage)
        conn.execute(
            "UPDATE trades SET signal_json = ? WHERE user_id = ? AND trade_date = ? AND ticker = ?",
            (json.dumps(sig, default=str), user_id, _today(), ticker),
        )


def merge_active_trade_signal(user_id: int, ticker: str, extra: dict) -> None:
    """Ergänzt das gespeicherte Signal eines aktiven Trades um zusätzliche Felder (z. B. den
    gewählten Optionskontrakt: option_symbol/entry_premium/delta/omega/contracts). Idempotent."""
    if not extra:
        return
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, signal_json FROM trades WHERE user_id = ? AND ticker = ? "
            "AND status IN ('active', 'broker_pending') "
            "ORDER BY trade_date DESC, id DESC LIMIT 1",
            (user_id, ticker),
        ).fetchone()
        if not row:
            return
        sig = json.loads(row["signal_json"])
        sig.update(extra)
        conn.execute("UPDATE trades SET signal_json = ? WHERE id = ?",
                     (json.dumps(sig, default=str), row["id"]))


# ── Dashboard-Zugang (Token-basierter Link) ─────────────────────────────────

def get_or_create_dashboard_token(user_id: int) -> str:
    """Gibt den persönlichen Dashboard-Token zurück, erzeugt ihn bei Bedarf."""
    with _connect() as conn:
        row = conn.execute("SELECT dashboard_token FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["dashboard_token"]:
            return row["dashboard_token"]
        token = secrets.token_urlsafe(24)
        conn.execute("UPDATE users SET dashboard_token = ? WHERE user_id = ?", (token, user_id))
    return token


def get_user_by_token(token: str) -> dict | None:
    """Löst einen Dashboard-Token zum Nutzerprofil auf (oder None bei ungültigem Token)."""
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE dashboard_token = ?", (token,)).fetchone()
    return _user_to_dict(row) if row else None


# ── Web-Sessions (Login-Cookies) ─────────────────────────────────────────────

def create_session(user_id: int, days: int = 30) -> str:
    """Legt eine Web-Session an und gibt das Session-Token (für das Cookie) zurück."""
    token = secrets.token_urlsafe(32)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) "
            "VALUES (?, ?, datetime('now', ?))",
            (token, user_id, f"{int(days):+d} days"),
        )
    return token


def user_id_for_session(token: str) -> int | None:
    """Gibt die user_id einer gültigen (nicht abgelaufenen) Session zurück, sonst None."""
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token = ? AND expires_at > datetime('now')",
            (token,),
        ).fetchone()
    return row["user_id"] if row else None


def delete_session(token: str):
    """Beendet eine Session (Logout)."""
    if not token:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_user_sessions(user_id: int) -> int:
    """Beendet ALLE Web-Sessions eines Nutzers ('überall abmelden'). Gibt die Anzahl zurück."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return cur.rowcount


def delete_expired_sessions() -> int:
    """Räumt abgelaufene Sessions auf. Gibt die Anzahl gelöschter Zeilen zurück."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
        return cur.rowcount


def reset_user_trades(user_id: int) -> int:
    """Setzt den Trading-Verlauf eines Nutzers zurück: löscht ALLE Trades (jeden Status),
    deren Intraday-Ticks und die In-App-Mitteilungen. Profil, Einstellungen und etwaige
    Alpaca-Zugangsdaten bleiben unangetastet. Gibt die Gesamtzahl gelöschter Zeilen zurück."""
    with _connect() as conn:
        total = 0
        for table in ("trades", "trade_ticks", "notifications"):
            cur = conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            total += cur.rowcount
    log.info(f"Reset: user_id={user_id} — {total} Zeile(n) gelöscht (trades/ticks/notifications).")
    return total


# ── Benachrichtigungs-Kanal & In-App-Benachrichtigungen ──────────────────────

def set_notify_channel(user_id: int, channel: str) -> str:
    """Setzt den Benachrichtigungs-Kanal ('telegram' | 'web' | 'both'). Gibt den gültigen Wert zurück."""
    channel = channel if channel in ("telegram", "web", "both") else "both"
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET notify_channel = ?, updated_at = datetime('now') WHERE user_id = ?",
            (channel, user_id),
        )
    return channel


def set_asset_pref(user_id: int, asset_pref: str) -> str:
    """Setzt die zuletzt gewählte Asset-Klasse (Dropdown auf der Website)."""
    asset_pref = (asset_pref or "stocks").strip() or "stocks"
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET asset_pref = ?, updated_at = datetime('now') WHERE user_id = ?",
            (asset_pref, user_id),
        )
    return asset_pref


def add_notification(user_id: int, title: str, body: str = "", type: str = "info") -> int:
    """Schreibt eine In-App-Benachrichtigung. Gibt die neue id zurück."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO notifications (user_id, type, title, body) VALUES (?, ?, ?, ?)",
            (user_id, type, title, body),
        )
        return cur.lastrowid


def get_notifications(user_id: int, limit: int = 50) -> list[dict]:
    """Letzte Benachrichtigungen eines Nutzers (neueste zuerst)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, type, title, body, read FROM notifications "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, int(limit)),
        ).fetchall()
    return [{"id": r["id"], "ts": r["ts"], "type": r["type"], "title": r["title"],
             "body": r["body"], "read": bool(r["read"])} for r in rows]


def unread_count(user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read = 0", (user_id,)
        ).fetchone()
    return row["n"] if row else 0


def mark_notifications_read(user_id: int):
    with _connect() as conn:
        conn.execute("UPDATE notifications SET read = 1 WHERE user_id = ?", (user_id,))


# ── Strategie-Konfiguration (Web-Editor + Backtest/Live-Overrides) ────────────

def _strategy_config_to_dict(row: sqlite3.Row) -> dict:
    params = {}
    try:
        params = json.loads(row["params_json"] or "{}") if row and row["params_json"] else {}
    except Exception:
        params = {}
    return {
        "key": row["key"],
        "label": row["label"],
        "description": row["description"],
        "params": params,
        "enabled": bool(row["enabled"]),
        "updated_at": row["updated_at"],
    }


def list_strategy_configs() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key, label, description, params_json, enabled, updated_at FROM strategy_configs ORDER BY key ASC"
        ).fetchall()
    return [_strategy_config_to_dict(r) for r in rows]


def get_strategy_config(key: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT key, label, description, params_json, enabled, updated_at FROM strategy_configs WHERE key = ?",
            (key,),
        ).fetchone()
    return _strategy_config_to_dict(row) if row else None


def upsert_strategy_config(key: str, label: str, description: str, params: dict | None = None,
                          enabled: bool = True) -> dict:
    params = params or {}
    with _connect() as conn:
        conn.execute(
            """INSERT INTO strategy_configs (key, label, description, params_json, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   label = excluded.label,
                   description = excluded.description,
                   params_json = excluded.params_json,
                   enabled = excluded.enabled,
                   updated_at = datetime('now')""",
            (key, label, description, json.dumps(params, default=str), 1 if enabled else 0),
        )
    row = get_strategy_config(key)
    return row or {"key": key, "label": label, "description": description, "params": params, "enabled": enabled}


def search_strategy_configs(query: str) -> list[dict]:
    q = (query or "").strip().lower()
    rows = list_strategy_configs()
    if not q:
        return rows
    out = []
    for row in rows:
        blob = " ".join([
            row["key"], row["label"], row["description"],
            json.dumps(row.get("params") or {}, sort_keys=True),
        ]).lower()
        if q in blob:
            out.append(row)
    return out


# ── Trade-Tracking (ersetzt TradeTracker, jetzt pro user_id) ───────────────

def _trade_to_dict(row: sqlite3.Row) -> dict:
    out = {
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
    }
    keys = set(row.keys())
    for key in ("broker_order_id", "broker_status", "broker_filled_qty",
                "broker_filled_avg_price", "broker_updated_at"):
        out[key] = row[key] if key in keys else None
    return out


def has_trade_today(user_id: int, ticker: str) -> bool:
    """True, wenn für diese Aktie heute bereits ein Signal/Trade existiert (egal welcher Status)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM trades WHERE user_id = ? AND trade_date = ? AND ticker = ? LIMIT 1",
            (user_id, _today(), ticker),
        ).fetchone()
    return row is not None


def has_open_position(user_id: int, ticker: str) -> bool:
    """Duplikat-Schutz fürs Senden: heute schon ein Datensatz ODER ein über Nacht offener
    (aktiver) Trade dieser Aktie (egal welches Datum)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM trades WHERE user_id = ? AND ticker = ? "
            "AND (trade_date = ? OR status = 'active') LIMIT 1",
            (user_id, ticker, _today()),
        ).fetchone()
    return row is not None


def add_pending(user_id: int, signal: dict, message_id: int) -> bool:
    """Fügt einen vorgemerkten Trade hinzu. Gibt True zurück, wenn neu angelegt;
    False, wenn für diese Aktie heute bereits ein Datensatz existiert (Duplikat-Schutz —
    ein bereits aktiver/abgeschlossener Trade wird NICHT auf 'pending' zurückgesetzt)."""
    ticker = signal["ticker"]
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json, message_id, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')
               ON CONFLICT (user_id, trade_date, ticker) DO NOTHING""",
            (user_id, _today(), ticker, signal["direction"], json.dumps(signal, default=str), message_id),
        )
        created = cur.rowcount > 0
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
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? AND trade_date = ? AND ticker = ?",
            (user_id, _today(), ticker),
        ).fetchone()
        if not row or row["status"] != "pending":
            return None

        signal = json.loads(row["signal_json"])
        try:
            info = yf.Ticker(ticker).fast_info
            entry_price = float(info.last_price)
        except Exception:
            entry_price = float(signal["price"])

        conn.execute(
            "UPDATE trades SET status = ?, entry = ? WHERE id = ?",
            (status, entry_price, row["id"]),
        )
    log.info(f"Trade aktiviert: user_id={user_id} {ticker} @ ${entry_price:.2f} ({status})")
    return get_trade(user_id, ticker)


def mark_broker_pending(user_id: int, ticker: str, *, order_id: str | None, broker_status: str | None) -> bool:
    """Speichert, dass die Broker-Order angenommen, aber noch nicht gefüllt ist."""
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE trades
               SET status = 'broker_pending', broker_order_id = ?, broker_status = ?,
                   broker_updated_at = datetime('now')
               WHERE user_id = ? AND ticker = ? AND status IN ('broker_pending', 'active')""",
            (order_id, broker_status, user_id, ticker),
        )
        return cur.rowcount > 0


def mark_broker_filled(user_id: int, ticker: str, *, broker_status: str = "filled",
                       filled_qty: float | None = None, filled_avg_price: float | None = None) -> bool:
    """Macht aus einer broker_pending-Order erst nach tatsächlichem Fill einen aktiven Trade."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, entry FROM trades WHERE user_id = ? AND ticker = ? AND status = 'broker_pending' "
            "ORDER BY trade_date DESC, id DESC LIMIT 1",
            (user_id, ticker),
        ).fetchone()
        if not row:
            return False
        entry = float(filled_avg_price or row["entry"] or 0)
        cur = conn.execute(
            """UPDATE trades
               SET status = 'active', entry = ?, broker_status = ?, broker_filled_qty = ?,
                   broker_filled_avg_price = ?, broker_updated_at = datetime('now')
               WHERE id = ?""",
            (entry, broker_status, filled_qty, filled_avg_price, row["id"]),
        )
        return cur.rowcount > 0


def mark_broker_failed(user_id: int, ticker: str, *, broker_status: str | None) -> bool:
    """Markiert eine nicht ausgeführte Broker-Order; sie darf nicht als aktiver Trade erscheinen."""
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE trades
               SET status = 'broker_failed', broker_status = ?, broker_updated_at = datetime('now')
               WHERE user_id = ? AND ticker = ? AND status IN ('broker_pending', 'active')""",
            (broker_status, user_id, ticker),
        )
        return cur.rowcount > 0


def mark_broker_closing(user_id: int, ticker: str, *, order_id: str | None, broker_status: str | None) -> bool:
    """Speichert, dass eine Broker-Schließung angestoßen wurde, die Position aber noch offen ist."""
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE trades
               SET status = 'broker_closing', broker_order_id = ?, broker_status = ?,
                   broker_updated_at = datetime('now')
               WHERE user_id = ? AND ticker = ? AND status IN ('active', 'broker_closing')""",
            (order_id, broker_status, user_id, ticker),
        )
        return cur.rowcount > 0


def mark_broker_close_failed(user_id: int, ticker: str, *, broker_status: str | None) -> bool:
    """Bringt einen fehlgeschlagenen Sell-Versuch wieder in den aktiven Zustand zurück."""
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE trades
               SET status = 'active', broker_status = ?, broker_updated_at = datetime('now')
               WHERE user_id = ? AND ticker = ? AND status = 'broker_closing'""",
            (broker_status, user_id, ticker),
        )
        return cur.rowcount > 0


def reject_trade(user_id: int, ticker: str) -> bool:
    """Markiert den heutigen Trade als abgelehnt. Gibt True zurück, falls ein Datensatz aktualisiert wurde."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE trades SET status = 'rejected' WHERE user_id = ? AND trade_date = ? AND ticker = ? AND status = 'pending'",
            (user_id, _today(), ticker),
        )
        return cur.rowcount > 0


def expire_trade(user_id: int, ticker: str) -> bool:
    """Markiert einen noch ausstehenden Trade als abgelaufen (Start-Zeitfenster verstrichen).
    Gibt True zurück, falls ein Datensatz aktualisiert wurde (also noch 'pending' war)."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE trades SET status = 'expired' WHERE user_id = ? AND trade_date = ? AND ticker = ? AND status = 'pending'",
            (user_id, _today(), ticker),
        )
        return cur.rowcount > 0


def get_active_trades(user_id: int) -> list[dict]:
    """Gibt ALLE aktiven Trades des Nutzers zurück (auch über Nacht gehaltene, datumsunabhängig)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? AND status = 'active' ORDER BY trade_date ASC, id ASC",
            (user_id,),
        ).fetchall()
    return [_trade_to_dict(r) for r in rows]


def get_pending_trades(user_id: int) -> list[dict]:
    """Gibt alle heute noch ausstehenden (pending) Trades des Nutzers zurück."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? AND trade_date = ? AND status = 'pending'",
            (user_id, _today()),
        ).fetchall()
    return [_trade_to_dict(r) for r in rows]


def get_broker_pending_trades(user_id: int) -> list[dict]:
    """Broker-Orders, die angenommen, aber noch nicht tatsächlich gefüllt wurden."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? AND status = 'broker_pending' ORDER BY trade_date ASC, id ASC",
            (user_id,),
        ).fetchall()
    return [_trade_to_dict(r) for r in rows]


def get_broker_closing_trades(user_id: int) -> list[dict]:
    """Broker-Schließungen, die angestoßen wurden, aber noch nicht final bestätigt sind."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? AND status = 'broker_closing' ORDER BY trade_date ASC, id ASC",
            (user_id,),
        ).fetchall()
    return [_trade_to_dict(r) for r in rows]


def get_trade(user_id: int, ticker: str) -> dict | None:
    """Relevantester Trade einer Aktie: aktiver (über Nacht gehaltener) zuerst, sonst der heutige.
    So funktioniert Verkaufen/Hebel auch bei datumsübergreifend offenen Trades."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? AND ticker = ? AND (status = 'active' OR trade_date = ?) "
            "ORDER BY (status = 'active') DESC, trade_date DESC, id DESC LIMIT 1",
            (user_id, ticker, _today()),
        ).fetchone()
    return _trade_to_dict(row) if row else None


def close_all(user_id: int, results: list[dict]):
    """Schließt die ausgewerteten Trades des Nutzers (matcht den AKTIVEN Trade je Aktie,
    datumsunabhängig — auch über Nacht gehaltene)."""
    with _connect() as conn:
        for r in results:
            conn.execute(
                """UPDATE trades SET status = 'closed', exit = ?, pnl_eur = ?, pnl_pct = ?
                   WHERE user_id = ? AND ticker = ? AND status IN ('active', 'broker_closing')""",
                (r["exit"], r["pnl_eur"], r["pnl_pct"], user_id, r["ticker"]),
            )
    log.info(f"user_id={user_id}: {len(results)} Trades geschlossen.")


def get_history(user_id: int, days: int = 30) -> list[dict]:
    """Gibt die abgeschlossenen Trades der letzten N Tage zurück (neueste zuerst)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM trades
               WHERE user_id = ? AND status = 'closed' AND trade_date >= date('now', ?)
               ORDER BY trade_date DESC""",
            (user_id, f"-{days} days"),
        ).fetchall()
    return [_trade_to_dict(r) for r in rows]


def get_closed_trades(user_id: int) -> list[dict]:
    """Alle abgeschlossenen Trades des Nutzers, älteste zuerst (für Equity-Kurve & Statistik)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM trades
               WHERE user_id = ? AND status = 'closed'
               ORDER BY trade_date ASC, id ASC""",
            (user_id,),
        ).fetchall()
    return [_trade_to_dict(r) for r in rows]


# ── Intraday-Ticks (Kurs- & Stärke-Verlauf je aktivem Trade) ────────────────

def add_tick(user_id: int, ticker: str, price: float | None, strength: float | None):
    """Schreibt einen Verlaufspunkt (Kurs + Signal-Stärke) für einen aktiven Trade."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO trade_ticks (user_id, trade_date, ticker, price, strength) VALUES (?, ?, ?, ?, ?)",
            (user_id, _today(), ticker, price, strength),
        )


def get_today_ticks(user_id: int) -> dict:
    """Gibt die heutigen Verlaufspunkte je Ticker zurück: { ticker: [{ts, price, strength}, ...] }."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT ticker, ts, price, strength FROM trade_ticks
               WHERE user_id = ? AND trade_date = ?
               ORDER BY ts ASC""",
            (user_id, _today()),
        ).fetchall()
    series: dict[str, list] = {}
    for r in rows:
        series.setdefault(r["ticker"], []).append(
            {"ts": r["ts"], "price": r["price"], "strength": r["strength"]}
        )
    return series
