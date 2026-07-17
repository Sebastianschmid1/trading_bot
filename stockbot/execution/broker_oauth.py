"""Alpaca-OAuth-Verbindungs-Seam (PLAT-007, W4.4).

**Additiv/opt-in.** Der bestehende API-Key-Pfad (`config.ALPACA_API_KEY`/`broker/client.py`)
bleibt unberührt. Diese Schicht verwaltet ausschließlich die Broker-VERBINDUNG (Token-Haltung),
NICHT das Trade-Verhalten oder die TSAFE-Sperren. Tokens werden verschlüsselt (Fernet über den
DB-Seam) gespeichert, nie im Klartext/in Logs. Paper- und Live-Verbindungen sind getrennt
(`mode`); eine Live-Verbindung ist reine Datenhaltung und ändert am Live-Kill-Switch nichts.

Widerruf (`revoke`) ruft den Alpaca-Revoke-Endpoint über einen INJIZIERBAREN Client auf und
markiert die Verbindung lokal als widerrufen. Der Default-Client ist bewusst `None`
(reiner lokaler Widerruf) — ein echter, netzwerkender Revoker wird explizit übergeben, damit
kein ungetesteter Netzcode im broker-nahen Pfad als Default läuft.
"""

from __future__ import annotations

import logging

from stockbot.core import db

log = logging.getLogger(__name__)

VALID_MODES = ("paper", "live")

# Minimal nötige Alpaca-OAuth-Scopes — nur so viel wie für Konto/Trading/Daten erforderlich.
MINIMAL_SCOPES = ("account:write", "trading", "data")


def _check_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(f"mode muss eines von {VALID_MODES} sein, war {mode!r}")


def _scope_str(scopes) -> str:
    if isinstance(scopes, str):
        return scopes
    return ",".join(str(s) for s in scopes)


def store_connection(*, user_id: int, mode: str, access_token: str,
                     refresh_token: str | None = None, scopes=MINIMAL_SCOPES,
                     expires_at: str | None = None) -> None:
    """Legt/ersetzt die (verschlüsselte) OAuth-Verbindung für (user_id, mode) an."""
    _check_mode(mode)
    if not access_token:
        raise ValueError("access_token fehlt")
    db.store_broker_oauth_connection(
        user_id=user_id, mode=mode, access_token=access_token,
        refresh_token=refresh_token, scopes=_scope_str(scopes), expires_at=expires_at,
    )


def get_connection(user_id: int, mode: str) -> dict | None:
    """Aktive (nicht widerrufene) Verbindung entschlüsselt, oder None."""
    _check_mode(mode)
    return db.get_broker_oauth_connection(user_id, mode)


def is_connected(user_id: int, mode: str) -> bool:
    return get_connection(user_id, mode) is not None


def disconnect(user_id: int, mode: str) -> None:
    """Lokale Verbindung löschen (kein Broker-Revoke)."""
    _check_mode(mode)
    db.disconnect_broker_oauth_connection(user_id, mode)


def revoke(user_id: int, mode: str, revoke_client=None) -> bool:
    """Widerruft die Verbindung: optional Broker-Revoke via injizierbarem Client, dann lokal sperren.

    `revoke_client` muss eine Methode `revoke(access_token)` haben. Gibt True zurück, wenn eine
    aktive Verbindung widerrufen wurde, sonst False (keine Verbindung vorhanden). Nach dem
    Widerruf liefert `get_connection` None und die Tokens sind überschrieben — der Nutzer kann so
    den Brokerzugriff sofort entziehen (Gate-P9-Kriterium).
    """
    _check_mode(mode)
    conn = db.get_broker_oauth_connection(user_id, mode)
    if conn is None:
        return False
    if revoke_client is not None:
        revoke_client.revoke(conn["access_token"])
    else:
        log.warning("OAuth-Revoke für user=%s mode=%s ohne Broker-Client — nur lokaler Widerruf.",
                    user_id, mode)
    db.revoke_broker_oauth_connection(user_id, mode)
    return True
