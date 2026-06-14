"""
Watchlist-Aktionen als framework-neutrale Services: Hinzufügen (mit Validierung + „Meinten Sie?")
und Entfernen.

Blockierend (yfinance/Alpaca/LLM) — der Aufrufer lagert sie bei Bedarf in einen Thread aus
(im Telegram-Bot via asyncio.to_thread). KEIN Telegram-/HTTP-Bezug.
"""

from stockbot.core import db
from stockbot.market import lookup
from stockbot.broker import client as broker
from stockbot.ai import llm_ranker


def add_to_watchlist(user_id: int, symbol: str, alpaca_client=None) -> dict:
    """Prüft `symbol` und nimmt es bei Erfolg in die Watchlist auf.

    Rückgabe:
      gefunden    → {"status": "added", "info": {...}, "asset": {...}|None, "watchlist": [...]}
      nicht gef.  → {"status": "not_found", "symbol": "XYZ", "suggestions": ["AAPL", ...]}
    `alpaca_client` (optional): wenn gesetzt, wird zusätzlich die Alpaca-Handelbarkeit geprüft.
    """
    info = lookup.validate_ticker(symbol)
    if info.get("ok"):
        wl = db.add_watchlist_tickers(user_id, [info["symbol"]])
        asset = broker.get_asset_info(info["symbol"], alpaca_client) if alpaca_client is not None else None
        return {"status": "added", "info": info, "asset": asset, "watchlist": wl}

    sym = (symbol or "").strip().upper()
    hits = lookup.search_symbols(sym)
    suggestions = [h["symbol"] for h in hits]
    if not suggestions:
        suggestions = llm_ranker.suggest_tickers(sym)
    suggestions = [s for s in suggestions if s != sym][:3]
    return {"status": "not_found", "symbol": sym, "suggestions": suggestions}


def remove_from_watchlist(user_id: int, symbol: str) -> list[str]:
    """Entfernt ein Symbol aus der Watchlist. Gibt die neue Liste zurück."""
    return db.remove_watchlist_ticker(user_id, symbol)
