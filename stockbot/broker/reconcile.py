"""
Positions-Abgleich: Bot-Sicht (aktive Trades in der DB) gegen die echten Alpaca-Positionen.

Wird nach jedem Broker-Vorgang (Kauf/Schließung) aufgerufen. Findet Abweichungen — Symbole, die
der Bot offen führt, aber Alpaca nicht (und umgekehrt) — und baut einen ausführlichen Bericht.
Der Aufrufer (Telegram-Handler) verschickt daraus das Error-Log.

`diff_positions` ist rein (gut testbar); `reconcile_user` kapselt die DB-/Broker-Zugriffe.
`sweep_missing_positions` schließt zusätzlich Trades, deren Broker-Position nach einer Grace-Phase
verschwunden ist (z. B. manuell im Broker verkauft).
"""

from datetime import datetime

from stockbot.core.evaluator import get_current_price, trade_pnl
from stockbot.core import db
from stockbot.broker import client as broker


def bot_symbol(trade: dict) -> str:
    """Das Broker-Symbol, unter dem ein aktiver Trade bei Alpaca steht: bei Options der
    Kontrakt (`option_symbol`), sonst der Aktien-Ticker."""
    sig = trade.get("signal") or {}
    return sig.get("option_symbol") or trade["ticker"]


def diff_positions(bot_syms: set[str], broker_syms: set[str]) -> dict:
    """Vergleicht zwei Symbol-Mengen. Rückgabe:
    {"ok": bool, "only_bot": [...], "only_broker": [...]}.
    """
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


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(ts, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def reconcile_user(user: dict, client) -> dict:
    """Gleicht die aktiven Trades des Nutzers gegen die offenen Alpaca-Positionen ab.
    Rückgabe: {"ok", "only_bot", "only_broker", "detail"}. Robust — wirft nie hart.
    """
    bot_syms = {bot_symbol(t) for t in db.get_active_trades(user["user_id"])}
    broker_syms = {p["symbol"] for p in broker.list_positions(client)}
    diff = diff_positions(bot_syms, broker_syms)
    diff["detail"] = _format(diff)
    return diff


def sweep_missing_positions(user: dict, client, *, grace_sec: int = 300) -> dict:
    """Schließt aktive Bot-Trades, deren Alpaca-Position nicht mehr existiert.

    Das ist der automatische "manuell verkauft"-Abgleich: verschwindet die Position im Broker,
    markiert der Bot den Trade nach der Grace-Phase als geschlossen.

    Rückgabe: {"closed": [...], "skipped": [...], "detail": str}
    """
    active = db.get_active_trades(user["user_id"])
    broker_positions = {p["symbol"]: p for p in broker.list_positions(client)}
    now = datetime.utcnow()
    closed: list[dict] = []
    skipped: list[dict] = []

    for trade in active:
        sym = bot_symbol(trade)
        if sym in broker_positions:
            continue

        updated = _parse_ts(trade.get("broker_updated_at")) or _parse_ts(trade.get("created_at"))
        age_sec = int((now - updated).total_seconds()) if updated else grace_sec
        if age_sec < grace_sec:
            skipped.append({"ticker": trade["ticker"], "symbol": sym, "age_sec": age_sec})
            continue

        exit_price = get_current_price(trade["ticker"], float(trade.get("entry") or 0.0))
        if exit_price is None:
            exit_price = float(trade.get("entry") or 0.0)
        pnl_pct, pnl_eur = trade_pnl(trade, exit_price, user.get("trade_size_eur") or 0.0)
        db.close_all(
            user["user_id"],
            [{"ticker": trade["ticker"], "exit": exit_price, "pnl_eur": pnl_eur, "pnl_pct": pnl_pct}],
            broker_status="reconciled_missing_position",
        )
        closed.append({"ticker": trade["ticker"], "symbol": sym, "exit": exit_price, "age_sec": age_sec})

    detail = "Keine fehlenden Positionen gefunden." if not closed else (
        "Geschlossen: " + ", ".join(f"{c['ticker']}" for c in closed)
    )
    return {"closed": closed, "skipped": skipped, "detail": detail}
