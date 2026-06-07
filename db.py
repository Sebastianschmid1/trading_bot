"""
SQLite-Persistenz für Multi-User-Betrieb
Ersetzt tracker.py — speichert Nutzerprofile (inkl. verschlüsselter
Broker-Zugangsdaten) und Demo-Trades, jeweils pro user_id (== Telegram chat_id).
"""

import sqlite3
import json
import logging
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
"""


def init_db():
    """Legt data/-Ordner und Tabellen an (idempotent). Beim Start einmal aufrufen."""
    DB_FILE.parent.mkdir(exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)


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
    }


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
    }


def add_pending(user_id: int, signal: dict, message_id: int):
    """Fügt einen vorgemerkten Trade hinzu (noch nicht bestätigt)."""
    ticker = signal["ticker"]
    with _connect() as conn:
        conn.execute(
            """INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json, message_id, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')
               ON CONFLICT (user_id, trade_date, ticker) DO UPDATE SET
                   direction = excluded.direction,
                   signal_json = excluded.signal_json,
                   message_id = excluded.message_id,
                   status = 'pending'""",
            (user_id, _today(), ticker, signal["direction"], json.dumps(signal, default=str), message_id),
        )
    log.info(f"Trade vorgemerkt: user_id={user_id} {ticker}")


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
