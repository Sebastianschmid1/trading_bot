"""Sichere Telegram-Callbacks (W7): opaque, serverseitig aufgelöste Tokens.

Statt Aktion + Daten direkt in die (manipulierbare, wiederholbare) `callback_data` zu schreiben,
gibt `issue()` ein OPAQUE Token aus, das serverseitig auf (Aktion, Payload) abgebildet ist —
gebunden an den Nutzer, mit Ablauf und EINMALIGER Verwendung. `resolve()` prüft alles und gibt
die Aktion nur einmal frei (idempotent gegen Doppel-Callback/Replay).

Reiner Sicherheits-Seam über den DB-Store; die Verdrahtung in die konkreten Bot-Handler
(Token in `InlineKeyboardButton.callback_data`, `resolve` im CallbackQuery-Handler) ist der
Integrationsschritt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stockbot.core import db

DEFAULT_TTL_SECONDS = 300


class CallbackSecurityError(RuntimeError):
    """Ein Callback-Token konnte nicht sicher aufgelöst werden."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue(user_id: int, action: str, payload: dict | None = None,
          ttl_seconds: int = DEFAULT_TTL_SECONDS, *, persistence=db) -> str:
    """Gibt ein opaques, nutzergebundenes Callback-Token mit Ablauf `ttl_seconds` zurück."""
    if not action:
        raise ValueError("action fehlt")
    expires_at = _fmt(_now() + timedelta(seconds=ttl_seconds))
    return persistence.issue_callback_token(
        user_id=user_id, action=action, payload=payload or {}, expires_at=expires_at)


def resolve(token: str, user_id: int, *, persistence=db) -> tuple[str, dict]:
    """Löst ein Token EINMALIG auf → (action, payload). Wirft `CallbackSecurityError` sonst.

    Gründe: 'unknown' (gefälscht/gelöscht), 'used' (Doppel-Callback/Replay), 'expired' (abgelaufen),
    'wrong_user' (an anderen Nutzer gebunden).
    """
    ok, action, payload, reason = persistence.resolve_callback_token(token, user_id, _fmt(_now()))
    if not ok:
        raise CallbackSecurityError(reason)
    return action, payload
