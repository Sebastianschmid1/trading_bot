"""
Positions-Abgleich: Bot-Sicht (aktive Trades in der DB) gegen die echten Alpaca-Positionen.

Wird nach jedem Broker-Vorgang (Kauf/Schließung) aufgerufen. Findet Abweichungen — Symbole, die
der Bot offen führt, aber Alpaca nicht (und umgekehrt) — und baut einen ausführlichen Bericht.
Der Aufrufer (Telegram-Handler) verschickt daraus das Error-Log.

`diff_positions` ist rein (gut testbar); `reconcile_user` kapselt die DB-/Broker-Zugriffe.
`sweep_missing_positions` schließt zusätzlich Trades, deren Broker-Position nach einer Grace-Phase
verschwunden ist (z. B. manuell im Broker verkauft).
"""

import re
from datetime import datetime

from stockbot.core.evaluator import get_current_price, trade_pnl
from stockbot.core import db
from stockbot.broker import client as broker

# OCC-Optionssymbol, z. B. AAPL260116C00200000 = AAPL · 2026-01-16 · Call · Strike 200.0
_OCC_RE = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
                     r"(?P<cp>[CP])(?P<strike>\d{8})$")

# Trade-Status, die eine Position im Broker erklären (nicht erneut übernehmen).
_NON_TERMINAL = ("active", "broker_pending", "broker_closing")


def bot_symbol(trade: dict) -> str:
    """Das Broker-Symbol, unter dem ein aktiver Trade bei Alpaca steht: bei Options der
    Kontrakt (`option_symbol`), sonst der Aktien-Ticker."""
    sig = trade.get("signal") or {}
    return sig.get("option_symbol") or trade["ticker"]


def parse_occ_symbol(symbol: str) -> dict | None:
    """Zerlegt ein OCC-Optionssymbol in {underlying, expiry, type, strike}; None bei Aktien."""
    m = _OCC_RE.match((symbol or "").strip())
    if not m:
        return None
    return {
        "underlying": m["root"],
        "expiry": f"20{m['yy']}-{m['mm']}-{m['dd']}",
        "type": "call" if m["cp"] == "C" else "put",
        "strike": int(m["strike"]) / 1000.0,
    }


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


def tracked_symbols(user_id: int) -> set[str]:
    """Alle Broker-Symbole, die der Bot aktuell (nicht-terminal) führt — aktive Trades plus
    Orders, die gerade gefüllt oder geschlossen werden. Damit wird eine Position, die zu einer
    laufenden Order gehört, NICHT fälschlich als verwaist übernommen."""
    syms: set[str] = set()
    for getter in (db.get_active_trades, db.get_broker_pending_trades, db.get_broker_closing_trades):
        for t in getter(user_id):
            syms.add(bot_symbol(t))
    return syms


def _signal_for_position(pos: dict) -> tuple[str, dict]:
    """Baut (ticker, signal) für eine zu übernehmende Broker-Position.

    Aktien → Ticker = Symbol, lineares Modell ohne Hebel. Options → Underlying aus dem
    OCC-Symbol, Kontraktfelder fürs options-bewusste P&L. SL/TP bewusst 'aus' (der Bot kennt
    die ursprüngliche Absicht nicht und soll die Position nicht versehentlich schließen)."""
    sym = pos["symbol"]
    avg = float(pos.get("avg_entry") or 0.0)
    qty = abs(float(pos.get("qty") or 0.0))
    signal = {"strategy": "adopted", "sl_tp_mode": "aus", "leverage": 1.0,
              "price": avg, "direction": "long", "adopted": True}
    occ = parse_occ_symbol(sym)
    if occ:
        signal.update({
            "option_symbol": sym, "strike": occ["strike"], "expiry": occ["expiry"],
            "entry_premium": avg, "contracts": int(qty) or 1, "omega": 1.0, "delta": None,
        })
        return occ["underlying"], signal
    return sym, signal


def adopt_orphan_positions(user: dict, client) -> dict:
    """Übernimmt Broker-Positionen, die der Bot NICHT führt, als aktive Trades (Selbstheilung).

    Das ist der Gegenpart zu `sweep_missing_positions`: schließt die Lücke „im Broker offen,
    aber nicht im Bot" — etwa wenn eine Order beim Broker durchging, der Bot sie aber wegen
    eines Sende-/Antwortfehlers als fehlgeschlagen verbucht hat.

    Rückgabe: {"adopted": [...], "skipped": [...], "detail": str}. Robust — wirft nie hart."""
    tracked = tracked_symbols(user["user_id"])
    adopted: list[dict] = []
    skipped: list[str] = []
    for pos in broker.list_positions(client):
        sym = pos["symbol"]
        if sym in tracked:
            continue
        ticker, signal = _signal_for_position(pos)
        ok = db.adopt_active_trade(
            user["user_id"], ticker, entry=float(pos.get("avg_entry") or 0.0),
            signal=signal, filled_qty=pos.get("qty"))
        if ok:
            adopted.append({"symbol": sym, "ticker": ticker, "qty": pos.get("qty"),
                            "entry": float(pos.get("avg_entry") or 0.0)})
        else:
            skipped.append(sym)   # schon getrackt (z. B. anderer Trade gleicher Aktie heute)
    detail = ("Übernommen: " + ", ".join(f"{a['ticker']} ({a['symbol']})" for a in adopted)
              if adopted else "Keine verwaisten Positionen.")
    return {"adopted": adopted, "skipped": skipped, "detail": detail}
