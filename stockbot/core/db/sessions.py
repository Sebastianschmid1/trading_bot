"""Dashboard-Token und Web-Sessions.

In der Datenbank liegt nur der SHA-256-Hash eines Session-Tokens — ein geleaktes Backup
ergibt damit keine gültigen Sessions. Das Klartext-Token existiert nur im Cookie.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from stockbot import config
from stockbot.core import db_backend

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


# ── Dashboard-Zugang (Token-basierter Link) ─────────────────────────────────

def get_or_create_dashboard_token(user_id: int) -> str:
    """Gibt den persönlichen Dashboard-Token zurück, erzeugt ihn bei Bedarf."""
    candidate = secrets.token_urlsafe(24)
    with db._database().transaction() as transaction:
        transaction.execute(
            """UPDATE users SET dashboard_token = :token
               WHERE user_id = :user_id AND dashboard_token IS NULL""",
            {"token": candidate, "user_id": user_id},
        )
        row = transaction.one(
            "SELECT dashboard_token FROM users WHERE user_id = :user_id", {"user_id": user_id}
        )
    return row["dashboard_token"] if row else candidate


def rotate_dashboard_token(user_id: int) -> str:
    """Erzeugt einen NEUEN Dashboard-Token (der alte Link wird sofort ungültig).
    Für den Fall, dass ein Token-Link geleakt ist (Logs, Browser-Verlauf, Weitergabe)."""
    token = secrets.token_urlsafe(24)
    db._update_user("UPDATE users SET dashboard_token = :token, updated_at = :updated_at "
                 "WHERE user_id = :user_id", token=token, user_id=user_id)
    return token


def get_user_by_token(token: str) -> dict | None:
    """Löst einen Dashboard-Token zum Nutzerprofil auf (oder None bei ungültigem Token)."""
    if not token:
        return None
    database = db_backend.get_database(config.DB_BACKEND, db._connect)
    with database.transaction() as transaction:
        row = transaction.one(
            "SELECT * FROM users WHERE dashboard_token = :token", {"token": token}
        )
    return db._user_to_dict(row) if row else None


# ── Web-Sessions (Login-Cookies) ─────────────────────────────────────────────
# In der DB liegt nur der SHA-256-Hash des Tokens — ein DB-Leak (Backup, Kopie)
# ergibt damit keine gültigen Sessions. Das Klartext-Token existiert nur im Cookie.

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_token_hash(value: str | None) -> bool:
    """True, wenn `value` wie ein SHA-256-Hex-Digest aussieht (64 Hex-Zeichen)."""
    if not value or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value)


def create_session(user_id: int, days: int = 30) -> str:
    """Legt eine Web-Session an und gibt das Session-Token (für das Cookie) zurück."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=int(days))
    with db._database().transaction() as transaction:
        transaction.execute(
            """INSERT INTO sessions (token, user_id, expires_at, created_at)
               VALUES (:token, :user_id, :expires_at, :created_at)""",
            {"token": _hash_token(token), "user_id": user_id,
             "expires_at": db._utc_timestamp(expires_at), "created_at": db._utc_timestamp(now)},
        )
    return token


def user_id_for_session(token: str) -> int | None:
    """Gibt die user_id einer gültigen (nicht abgelaufenen) Session zurück, sonst None."""
    if not token:
        return None
    with db._database().transaction() as transaction:
        row = transaction.one(
            """SELECT user_id FROM sessions
               WHERE token = :token AND expires_at > :now""",
            {"token": _hash_token(token), "now": db._utc_timestamp()},
        )
    return row["user_id"] if row else None


def delete_session(token: str):
    """Beendet eine Session (Logout)."""
    if not token:
        return
    with db._database().transaction() as transaction:
        transaction.execute("DELETE FROM sessions WHERE token = :token", {"token": _hash_token(token)})


def delete_user_sessions(user_id: int) -> int:
    """Beendet ALLE Web-Sessions eines Nutzers ('überall abmelden'). Gibt die Anzahl zurück."""
    with db._database().transaction() as transaction:
        return transaction.execute("DELETE FROM sessions WHERE user_id = :user_id", {"user_id": user_id})


def delete_expired_sessions() -> int:
    """Räumt abgelaufene Sessions auf. Gibt die Anzahl gelöschter Zeilen zurück."""
    with db._database().transaction() as transaction:
        return transaction.execute(
            "DELETE FROM sessions WHERE expires_at <= :now", {"now": db._utc_timestamp()}
        )
