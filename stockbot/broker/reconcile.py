"""
Positions-Abgleich: Bot-Sicht (aktive Trades in der DB) gegen die echten Alpaca-Positionen.

Wird nach jedem Broker-Vorgang (Kauf/Schließung) aufgerufen. Findet Abweichungen — Symbole, die
der Bot offen führt, aber Alpaca nicht (und umgekehrt) — und baut einen ausführlichen Bericht.
Der Aufrufer (Telegram-Handler) verschickt daraus das Error-Log.

`diff_positions` ist rein (gut testbar); `reconcile_user` kapselt die DB-/Broker-Zugriffe.
"""

from stockbot.core import db
from stockbot.broker import client as broker


def bot_symbol(trade: dict) -> str:
    """Das Broker-Symbol, unter dem ein aktiver Trade bei Alpaca steht: bei Options der
    Kontrakt (`option_symbol`), sonst der Aktien-Ticker."""
    sig = trade.get("signal") or {}
    return sig.get("option_symbol") or trade["ticker"]


def diff_positions(bot_syms: set[str], broker_syms: set[str]) -> dict:
    """Vergleicht zwei Symbol-Mengen. Rückgabe:
    {"ok": bool, "only_bot": [...], "only_broker": [...]}."""
    only_bot = sorted(bot_syms - broker_syms)
    only_broker = sorted(broker_syms - bot_syms)
    return {"ok": not (only_bot or only_broker),
            "only_bot": only_bot, "only_broker": only_broker}


def _format(diff: dict) -> str:
    """Menschenlesbarer Abweichungs-Bericht (für Log + Telegram)."""
    lines = []
    if diff["only_bot"]:
        lines.append("• Im Bot offen, aber NICHT bei Alpaca: " + ", ".join(diff["only_bot"]))
    if diff["only_broker"]:
        lines.append("• Bei Alpaca offen, aber NICHT im Bot: " + ", ".join(diff["only_broker"]))
    return "\n".join(lines) if lines else "Keine Abweichung."


def reconcile_user(user: dict, client) -> dict:
    """Gleicht die aktiven Trades des Nutzers gegen die offenen Alpaca-Positionen ab.
    Rückgabe: {"ok", "only_bot", "only_broker", "detail"}. Robust — wirft nie hart."""
    bot_syms = {bot_symbol(t) for t in db.get_active_trades(user["user_id"])}
    broker_syms = {p["symbol"] for p in broker.list_positions(client)}
    diff = diff_positions(bot_syms, broker_syms)
    diff["detail"] = _format(diff)
    return diff
