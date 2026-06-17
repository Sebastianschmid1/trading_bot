"""
Trade-Aktionen als framework-neutrale Services: Annehmen, Ablehnen, Verkaufen, Hebel ändern.

Reine Domänenlogik (DB-Zustand + Kurs/P&L) — KEIN Telegram, KEINE Broker-Order und KEIN Versenden
von Nachrichten. Der Aufrufer (Telegram-Handler oder Web-Endpunkt) übernimmt Darstellung und
etwaige Broker-Ausführung anhand des zurückgegebenen Ergebnisses.
"""

from stockbot.core import db
from stockbot.core.evaluator import get_current_price, trade_pnl
from stockbot.config import DEFAULT_LEVERAGE


def accept_signal(user_id: int, signal: dict) -> dict:
    """Nimmt ein **on-demand** angefordertes Signal an: legt es als pending an und aktiviert es
    sofort (für die Website, wo der Nutzer ein gerade berechnetes Signal direkt startet).

    Erwartet ein Signal-Dict aus analyzer.analyze_ticker (mind. ticker/direction/price).
    Rückgabe wie accept_trade. {"ok": False, "reason": "unavailable"}, wenn heute für die
    Aktie bereits ein Trade existiert (Duplikat-Schutz greift in db.add_pending)."""
    sig = dict(signal)
    sig.setdefault("direction", "long")
    sig.setdefault("leverage", DEFAULT_LEVERAGE)
    sig.setdefault("strategy", "standard")
    if not sig.get("ticker") or sig.get("price") is None:
        return {"ok": False, "reason": "unavailable"}
    status = sig.pop("_accept_status", "active")
    db.add_pending(user_id, sig, message_id=0)
    return accept_trade(user_id, sig["ticker"], status=status)


def accept_trade(user_id: int, ticker: str, *, status: str = "active") -> dict:
    """Aktiviert ein ausstehendes Signal.

    Standard: pending → active (Demo-Modus). Bei echter Broker-Ausführung nutzt der Aufrufer
    `status='broker_pending'`; der Trade wird erst nach Alpaca-Fill zu `active`.

    Rückgabe: {"ok": True, "trade": …} oder {"ok": False, "reason": "expired"|"unavailable"}.
    """
    trade = db.activate_trade(user_id, ticker, status=status)
    if trade:
        return {"ok": True, "trade": trade}
    existing = db.get_trade(user_id, ticker)
    if existing and existing["status"] == "expired":
        return {"ok": False, "reason": "expired"}
    return {"ok": False, "reason": "unavailable"}


def reject_trade(user_id: int, ticker: str) -> bool:
    """Lehnt ein ausstehendes Signal ab. True bei Erfolg, sonst False (schon bearbeitet/unbekannt)."""
    return bool(db.reject_trade(user_id, ticker))


def attach_option_for_trade(user: dict, trade: dict, selector) -> dict | None:
    """Wählt bei Hebel>1 einen Optionskontrakt fürs Budget und schreibt ihn ins Signal des aktiven
    Trades (Grundlage der Options-Simulation/echten Order). `selector(budget, target_leverage)`
    liefert {option_symbol, strike, expiry, premium, delta, omega, qty} oder None (kapselt den
    Broker-Zugriff, damit dieser Service broker-frei bleibt). Idempotent.

    Rückgabe: die ins Signal geschriebenen Options-Felder oder None (kein Hebel/kein Kontrakt)."""
    sig = trade.get("signal") or {}
    leverage = float(sig.get("leverage", 1.0) or 1.0)
    if leverage <= 1.0 or sig.get("option_symbol"):
        return None
    entry = trade.get("entry") or sig.get("price")
    if not entry:
        return None
    opt = selector(float(user["trade_size_eur"]), leverage)
    if not opt or not opt.get("qty"):
        return None
    extra = {"option_symbol": opt["option_symbol"], "strike": opt["strike"], "expiry": opt["expiry"],
             "entry_premium": opt["premium"], "delta": opt["delta"], "omega": opt["omega"],
             "contracts": opt["qty"]}
    db.merge_active_trade_signal(user["user_id"], trade["ticker"], extra)
    trade["signal"] = {**sig, **extra}
    return extra


def set_pending_leverage(user_id: int, ticker: str, leverage: float) -> dict | None:
    """Ändert den Hebel eines noch ausstehenden Trades. Gibt den aktualisierten Trade zurück
    oder None, wenn der Trade nicht mehr ausstehend (also nicht mehr änderbar) ist."""
    trade = db.get_trade(user_id, ticker)
    if not trade or trade["status"] != "pending":
        return None
    db.set_trade_leverage(user_id, ticker, leverage)
    return db.get_trade(user_id, ticker)


def sell_trade(user_id: int, ticker: str, *, current_premium: float | None = None,
               broker_close: bool = False) -> dict:
    """Schließt einen aktiven Demo-Trade zum aktuellen Kurs (realisierter, gehebelter P&L).

    Rückgabe bei Erfolg: {"ok": True, trade, entry, current, leverage, pnl_pct, pnl_eur,
    entry_strength, exit_strength}. Sonst {"ok": False, "reason": "not_active"}.
    Schließt NUR den Demo-Trade in der DB — eine echte Broker-Position schließt der Aufrufer.
    Bei Options-Trades wird, wenn `current_premium` übergeben wird, nichtlinear bewertet,
    sonst über die Omega-Näherung (siehe evaluator.trade_pnl).
    Wenn `broker_close=True`, wird der Trade nur auf `broker_closing` gesetzt; das endgültige
    Schließen übernimmt der Aufrufer nach der Broker-Bestätigung.
    """
    user = db.get_user(user_id)
    trade = db.get_trade(user_id, ticker)
    if not user or not trade or trade["status"] != "active":
        return {"ok": False, "reason": "not_active"}

    entry = trade["entry"]
    leverage = trade.get("signal", {}).get("leverage", 1.0) or 1.0
    current = get_current_price(ticker, entry)
    pnl_pct, pnl_eur = trade_pnl(trade, current, user["trade_size_eur"],
                                 current_premium=current_premium)

    if broker_close:
        db.mark_broker_closing(user_id, ticker, order_id=None, broker_status="requested")
        pts = db.get_today_ticks(user_id).get(ticker, [])
        exit_strength = pts[-1].get("strength") if pts else None
        return {
            "ok": True,
            "trade": trade,
            "entry": entry,
            "current": current,
            "leverage": leverage,
            "pnl_pct": pnl_pct,
            "pnl_eur": pnl_eur,
            "entry_strength": trade.get("signal", {}).get("strength"),
            "exit_strength": exit_strength,
            "broker_close": True,
            "status": "broker_closing",
        }

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
