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
from urllib.parse import urlparse

from stockbot.core import db
from stockbot import config
from stockbot.config import TELEGRAM_TOKEN

SESSION_COOKIE = "sb_session"
SESSION_DAYS = 30


# ── CSRF-Schutz: Origin/Referer-Abgleich ─────────────────────────────────────

def is_same_origin(request) -> bool:
    """True, wenn ein state-ändernder Request NICHT eindeutig von einem fremden Origin kommt
    (CSRF-Schutz). Blockiert nur, wenn ein Origin/Referer vorhanden, parsebar und sein Host
    keiner der erlaubten Hosts ist (Host-Header, Request-Host, konfigurierte DASHBOARD_BASE_URL —
    funktioniert so auch hinter einem TLS-Reverse-Proxy).

    Bewusst tolerant: fehlt Origin/Referer ganz, oder ist der Origin opak ('null', z. B. im
    Telegram-In-App-Browser/Sandbox), wird NICHT blockiert — dort schützt weiterhin das
    SameSite=lax-Cookie gegen echte Cross-Site-POSTs. So gibt es keine Fehlalarme für
    legitime Nutzer."""
    src = request.headers.get("origin") or request.headers.get("referer")
    if not src:
        return True                              # kein Signal → auf SameSite=lax verlassen
    src_host = urlparse(src).hostname
    if not src_host:
        return True                              # 'null'/opak/unparsebar → nicht blockieren
    allowed = {h for h in (
        (request.headers.get("host") or "").split(":")[0],
        request.url.hostname or "",
        urlparse(config.DASHBOARD_BASE_URL or "").hostname or "",
    ) if h}
    return src_host in allowed


# ── Einfaches In-Memory-Rate-Limit (Login-Endpunkte) ─────────────────────────

_auth_hits: dict[str, list] = {}


def rate_ok(key: str, limit: int = 20, window: int = 60) -> bool:
    """True, solange `key` (z. B. Client-IP) im Zeitfenster unter dem Limit bleibt.
    Schützt die Login-Endpunkte gegen Missbrauch/Brute-Force."""
    now = time.time()
    # Speicher-Deckel: bei vielen Keys (Bot-Scans probieren zig IPs) abgelaufene Einträge räumen,
    # sonst wächst das Dict im Dauerbetrieb unbegrenzt.
    if len(_auth_hits) > 512:
        for stale in [k for k, v in _auth_hits.items() if not v or now - v[-1] >= window]:
            _auth_hits.pop(stale, None)
    hits = [t for t in _auth_hits.get(key, []) if now - t < window]
    hits.append(now)
    _auth_hits[key] = hits
    return len(hits) <= limit


def current_user(request) -> dict | None:
    """Aktueller Nutzer aus dem Session-Cookie (oder None, wenn nicht eingeloggt)."""
    token = request.cookies.get(SESSION_COOKIE)
    uid = db.user_id_for_session(token) if token else None
    return db.get_user(uid) if uid else None


def login_response(response, user_id: int):
    """Setzt das Session-Cookie für `user_id` auf einer bestehenden Response."""
    token = db.create_session(user_id, days=SESSION_DAYS)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=config.COOKIE_SECURE,
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
