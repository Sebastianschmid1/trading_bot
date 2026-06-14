"""
Trade-Aktionen als framework-neutrale Services: Annehmen, Ablehnen, Verkaufen, Hebel ändern.

Reine Domänenlogik (DB-Zustand + Kurs/P&L) — KEIN Telegram, KEINE Broker-Order und KEIN Versenden
von Nachrichten. Der Aufrufer (Telegram-Handler oder Web-Endpunkt) übernimmt Darstellung und
etwaige Broker-Ausführung anhand des zurückgegebenen Ergebnisses.
"""

from stockbot.core import db
from stockbot.core.evaluator import get_current_price, realized_pnl


def accept_trade(user_id: int, ticker: str) -> dict:
    """Aktiviert ein ausstehendes Signal (pending → active).

    Rückgabe: {"ok": True, "trade": …} oder {"ok": False, "reason": "expired"|"unavailable"}.
    """
    trade = db.activate_trade(user_id, ticker)
    if trade:
        return {"ok": True, "trade": trade}
    existing = db.get_trade(user_id, ticker)
    if existing and existing["status"] == "expired":
        return {"ok": False, "reason": "expired"}
    return {"ok": False, "reason": "unavailable"}


def reject_trade(user_id: int, ticker: str) -> bool:
    """Lehnt ein ausstehendes Signal ab. True bei Erfolg, sonst False (schon bearbeitet/unbekannt)."""
    return bool(db.reject_trade(user_id, ticker))


def set_pending_leverage(user_id: int, ticker: str, leverage: float) -> dict | None:
    """Ändert den Hebel eines noch ausstehenden Trades. Gibt den aktualisierten Trade zurück
    oder None, wenn der Trade nicht mehr ausstehend (also nicht mehr änderbar) ist."""
    trade = db.get_trade(user_id, ticker)
    if not trade or trade["status"] != "pending":
        return None
    db.set_trade_leverage(user_id, ticker, leverage)
    return db.get_trade(user_id, ticker)


def sell_trade(user_id: int, ticker: str) -> dict:
    """Schließt einen aktiven Demo-Trade zum aktuellen Kurs (realisierter, gehebelter P&L).

    Rückgabe bei Erfolg: {"ok": True, trade, entry, current, leverage, pnl_pct, pnl_eur,
    entry_strength, exit_strength}. Sonst {"ok": False, "reason": "not_active"}.
    Schließt NUR den Demo-Trade in der DB — eine echte Broker-Position schließt der Aufrufer.
    """
    user = db.get_user(user_id)
    trade = db.get_trade(user_id, ticker)
    if not user or not trade or trade["status"] != "active":
        return {"ok": False, "reason": "not_active"}

    entry = trade["entry"]
    leverage = trade.get("signal", {}).get("leverage", 1.0) or 1.0
    current = get_current_price(ticker, entry)
    pnl_pct, pnl_eur = realized_pnl(entry, current, trade["direction"],
                                    user["trade_size_eur"], leverage)
    db.close_all(user_id, [{"ticker": ticker, "exit": current,
                            "pnl_eur": pnl_eur, "pnl_pct": pnl_pct}])

    pts = db.get_today_ticks(user_id).get(ticker, [])
    exit_strength = pts[-1].get("strength") if pts else None
    return {
        "ok": True, "trade": trade, "entry": entry, "current": current, "leverage": leverage,
        "pnl_pct": pnl_pct, "pnl_eur": pnl_eur,
        "entry_strength": trade.get("signal", {}).get("strength"),
        "exit_strength": exit_strength,
    }
