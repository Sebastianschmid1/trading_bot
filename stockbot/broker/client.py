"""
Alpaca-Anbindung (Trading API, eigenes Konto) — Fundament.

Standard = PAPER (kein echtes Geld). Bricht den Bot nie: ohne Keys / ohne installiertes
`alpaca-py` / bei Fehlern geben die Funktionen sauber {ok: False, ...} zurück.

Order-Logik:
- Reguläre Handelszeit → **Bracket-Market-Order** (Einstieg + SL + TP in einem).
- Erweiterte Handelszeit (Pre-/After-Market) → Alpaca erlaubt **keine** Bracket/Market-Orders,
  nur **Limit + TimeInForce.DAY + extended_hours=True**. SL/TP werden dann NICHT mitgeschickt
  (der Bot überwacht sie selbst per Monitor).

Die Keys liegen NUR in .env (gitignored) — niemals committen/loggen.
"""

import logging

from stockbot import config

log = logging.getLogger(__name__)


def make_client(api_key: str, api_secret: str, paper: bool = True):
    """Baut einen Alpaca TradingClient aus expliziten Zugangsdaten (z. B. pro Nutzer).
    Gibt None zurück, wenn das Paket fehlt oder die Keys ungültig sind — wirft nie."""
    if not (api_key and api_secret):
        return None
    try:
        from alpaca.trading.client import TradingClient
        return TradingClient(api_key, api_secret, paper=paper)
    except Exception as e:
        log.warning(f"Alpaca-Client nicht verfügbar: {e}")
        return None


def _get_client(client=None):
    """Alpaca TradingClient (Paper per Default) oder None (deaktiviert / Paket fehlt / Keys fehlen).
    Fallback auf globale .env-Keys, wenn kein expliziter Client übergeben wird."""
    if client is not None:
        return client
    if not config.ALPACA_ENABLED:
        return None
    return make_client(config.ALPACA_API_KEY, config.ALPACA_API_SECRET, paper=config.ALPACA_PAPER)


def health_check(client=None) -> dict:
    """Selbsttest der Alpaca-Anbindung: Konto + Marktstatus."""
    if client is None:
        if not config.ALPACA_ENABLED:
            return {"ok": False, "detail": "Keine ALPACA_API_KEY/SECRET in .env — Alpaca ist aus."}
        client = _get_client()
        if client is None:
            return {"ok": False, "detail": "Alpaca-Client nicht verfügbar (Keys ungültig oder alpaca-py fehlt)."}
    try:
        acct = client.get_account()
        clock = client.get_clock()
        return {
            "ok": True,
            "paper": config.ALPACA_PAPER,
            "status": str(getattr(acct, "status", "")),
            "cash": float(getattr(acct, "cash", 0) or 0),
            "buying_power": float(getattr(acct, "buying_power", 0) or 0),
            "currency": str(getattr(acct, "currency", "USD")),
            "market_open": bool(getattr(clock, "is_open", False)),
            "next_open": str(getattr(clock, "next_open", "")),
            "next_close": str(getattr(clock, "next_close", "")),
            "detail": "OK",
        }
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def market_open(client=None) -> bool | None:
    """Ist der reguläre US-Markt laut Alpaca-Clock offen? None, wenn nicht abrufbar."""
    client = _get_client(client)
    if client is None:
        return None
    try:
        return bool(client.get_clock().is_open)
    except Exception:
        return None


def submit_order(symbol: str, qty: int, entry_price: float, stop_loss: float | None,
                 take_profit: float | None, *, extended_hours: bool = False, client=None) -> dict:
    """Sendet eine (Paper-)Kauforder. Bracket in regulärer Zeit, Limit-DAY in Extended Hours.
    Robust: gibt {ok, id|detail} zurück, wirft nie."""
    client = _get_client(client)
    if client is None:
        return {"ok": False, "detail": "Alpaca nicht aktiv."}
    qty = max(1, int(qty))
    try:
        from alpaca.trading.requests import (
            MarketOrderRequest, LimitOrderRequest, TakeProfitRequest, StopLossRequest)
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        if extended_hours:
            # Extended Hours: nur Limit + DAY, keine Bracket-Klammer → SL/TP managt der Bot
            req = LimitOrderRequest(
                symbol=symbol, qty=qty, side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY, limit_price=round(float(entry_price), 2),
                extended_hours=True,
            )
        else:
            kwargs = dict(symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC)
            if stop_loss and take_profit:
                kwargs.update(order_class=OrderClass.BRACKET,
                              take_profit=TakeProfitRequest(limit_price=round(float(take_profit), 2)),
                              stop_loss=StopLossRequest(stop_price=round(float(stop_loss), 2)))
            req = MarketOrderRequest(**kwargs)

        order = client.submit_order(req)
        log.info(f"Alpaca-Order {symbol} qty={qty} ext={extended_hours} → id={getattr(order, 'id', '?')}")
        return {"ok": True, "id": str(getattr(order, "id", "")),
                "detail": f"{symbol} ×{qty} ({'Limit/Ext' if extended_hours else 'Bracket'})"}
    except Exception as e:
        log.warning(f"Alpaca-Order {symbol} fehlgeschlagen: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def list_positions(client=None) -> list[dict]:
    client = _get_client(client)
    if client is None:
        return []
    try:
        return [{"symbol": p.symbol, "qty": float(p.qty), "avg_entry": float(p.avg_entry_price),
                 "unrealized_pl": float(getattr(p, "unrealized_pl", 0) or 0)}
                for p in client.get_all_positions()]
    except Exception as e:
        log.warning(f"Alpaca-Positionen nicht abrufbar: {e}")
        return []


def close_position(symbol: str, client=None) -> dict:
    client = _get_client(client)
    if client is None:
        return {"ok": False, "detail": "Alpaca nicht aktiv."}
    try:
        client.close_position(symbol)
        return {"ok": True, "detail": f"{symbol} geschlossen"}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
