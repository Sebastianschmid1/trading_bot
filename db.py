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
from pathlib import Path

from cryptography.fernet import Fernet
import yfinance as yf

from config import ENCRYPTION_KEY

log = logging.getLogger(__name__)
DB_FILE = Path("data/bot.db")

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
"""


def init_db():
    """Legt data/-Ordner und Tabellen an (idempotent). Beim Start einmal aufrufen."""
    DB_FILE.parent.mkdir(exist_ok=True)
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
        "market_region":    row["market_region"],
        "top_n_signals":    row["top_n_signals"],
        "sl_tp_mode":       row["sl_tp_mode"],
        "leverage":         row["leverage"],
        "auto_accept":      bool(row["auto_accept"]),
        "auto_universe":    bool(row["auto_universe"]),
        "strategy":         row["strategy"],
        "strategies":       _parse_strategies(row["strategy"]),
    }


def _parse_strategies(raw: str | None) -> list[str]:
    """Kommagetrennte Strategie-Liste aus der DB → Liste (mind. ein Eintrag)."""
    keys = [s.strip() for s in (raw or "").split(",") if s.strip()]
    return keys or ["standard"]


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
    """Setzt den Markt-Bereich des Nutzers (z. B. 'sp500', 'msci_world', 'emerging')."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET market_region = ?, updated_at = datetime('now') WHERE user_id = ?",
            (region, user_id),
        )


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


# ── Trade-Tracking (ersetzt TradeTracker, jetzt pro user_id) ───────────────

def _trade_to_dict(row: sqlite3.Row) -> dict:
    return {
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


def has_trade_today(user_id: int, ticker: str) -> bool:
    """True, wenn für diese Aktie heute bereits ein Signal/Trade existiert (egal welcher Status).
    Basis für den Duplikat-Schutz: pro Aktie/Tag nur ein Signal."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM trades WHERE user_id = ? AND trade_date = ? AND ticker = ? LIMIT 1",
            (user_id, _today(), ticker),
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


def activate_trade(user_id: int, ticker: str) -> dict | None:
    """Aktiviert einen pendenten Trade nach JA-Klick, holt den aktuellen Kurs. Gibt den aktualisierten Trade zurück."""
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
            "UPDATE trades SET status = 'active', entry = ? WHERE id = ?",
            (entry_price, row["id"]),
        )
    log.info(f"Trade aktiviert: user_id={user_id} {ticker} @ ${entry_price:.2f}")
    return get_trade(user_id, ticker)


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
    """Gibt alle heute aktiven Trades des Nutzers zurück."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? AND trade_date = ? AND status = 'active'",
            (user_id, _today()),
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


def get_trade(user_id: int, ticker: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? AND trade_date = ? AND ticker = ?",
            (user_id, _today(), ticker),
        ).fetchone()
    return _trade_to_dict(row) if row else None


def close_all(user_id: int, results: list[dict]):
    """Schließt die ausgewerteten Trades des Nutzers mit den Ergebnissen aus evaluate_trades()."""
    with _connect() as conn:
        for r in results:
            conn.execute(
                """UPDATE trades SET status = 'closed', exit = ?, pnl_eur = ?, pnl_pct = ?
                   WHERE user_id = ? AND trade_date = ? AND ticker = ?""",
                (r["exit"], r["pnl_eur"], r["pnl_pct"], user_id, _today(), r["ticker"]),
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
