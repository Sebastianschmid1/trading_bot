"""
Web-Authentifizierung für die Website (Phase 1).

Zwei Login-Wege, beide münden in eine Server-Session (DB-Tabelle `sessions`) als HTTP-only-Cookie:
  1. **Token-Bootstrap**: der bereits existierende private Dashboard-Link (`dashboard_token`)
     erzeugt eine Session — funktioniert sofort für alle Bestandsnutzer.
  2. **Login mit Telegram** (offizielles Widget): HMAC-verifiziert, mappt die Telegram-`user_id`
     direkt auf den bestehenden DB-Nutzer (keine neue Identität nötig).
"""

import time
import hmac
import hashlib

from stockbot.core import db
from stockbot.config import TELEGRAM_TOKEN

SESSION_COOKIE = "sb_session"
SESSION_DAYS = 30


def current_user(request) -> dict | None:
    """Aktueller Nutzer aus dem Session-Cookie (oder None, wenn nicht eingeloggt)."""
    token = request.cookies.get(SESSION_COOKIE)
    uid = db.user_id_for_session(token) if token else None
    return db.get_user(uid) if uid else None


def login_response(response, user_id: int):
    """Setzt das Session-Cookie für `user_id` auf einer bestehenden Response."""
    token = db.create_session(user_id, days=SESSION_DAYS)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_DAYS * 24 * 3600, path="/")
    return response


def logout_response(request, response):
    """Beendet die Session (DB + Cookie)."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def verify_telegram_login(data: dict) -> int | None:
    """Verifiziert die Telegram-Login-Widget-Daten per HMAC-SHA256.
    Gibt die Telegram-user_id zurück (== unsere user_id) oder None bei Ungültigkeit/zu alt."""
    if not TELEGRAM_TOKEN:
        return None
    received = data.get("hash")
    if not received:
        return None
    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data) if k != "hash")
    secret_key = hashlib.sha256(TELEGRAM_TOKEN.encode()).digest()
    calc = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    try:
        if time.time() - int(data.get("auth_date", 0)) > 86400:   # max. 1 Tag alt
            return None
        return int(data["id"])
    except (TypeError, ValueError, KeyError):
        return None
