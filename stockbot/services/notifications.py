"""
Kanalübergreifende Benachrichtigungen.

Telegram-Push macht der Bot weiterhin direkt; diese Funktion schreibt zusätzlich eine
**In-App-Benachrichtigung** für die Website — abhängig vom `notify_channel` des Nutzers
('web' | 'both' → schreiben; 'telegram' → nicht). So verpasst niemand etwas, egal welchen
Kanal er nutzt (Phase 2 des Telegram→Website-Konzepts).
"""

from stockbot.core import db


def notify(user_id: int, title: str, body: str = "", type: str = "info", *, user: dict | None = None) -> bool:
    """Legt eine In-App-Benachrichtigung an, wenn der Nutzer Web-Benachrichtigungen nicht
    abgewählt hat. Gibt True zurück, wenn geschrieben wurde."""
    u = user if user is not None else db.get_user(user_id)
    channel = (u or {}).get("notify_channel", "both")
    if channel in ("web", "both"):
        db.add_notification(user_id, title, body, type)
        return True
    return False
