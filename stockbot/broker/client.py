"""
Alpaca-Anbindung (Trading API, eigenes Konto) — Fundament.

Standard = PAPER (kein echtes Geld). Bricht den Bot nie: ohne Keys / ohne installiertes
`alpaca-py` / bei Fehlern geben die Funktionen sauber {ok: False, ...} zurück.

Order-Logik (Kauf = `submit_buy`):
- Reguläre Handelszeit → **Market-Notional-Order** (Dollar-Betrag = Budget×Hebel) → Alpaca
  kauft auch **Bruchteile** exakt fürs Budget. TimeInForce.DAY, kein Bracket.
- Erweiterte Handelszeit (Pre-/After-Market) → Alpaca erlaubt dort **keine** Bruchteile/Notional,
  nur **ganze Aktien als Limit + TimeInForce.DAY + extended_hours=True**.

SL/TP werden NIE an den Broker geschickt — der Bot überwacht sie selbst (Monitor) und schließt
die echte Position über `close_position`. Mit `get_order_status` wird die *tatsächliche*
Ausführung (Fill) bestätigt.

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


def submit_buy(symbol: str, *, notional: float | None = None, qty: float | None = None,
               limit_price: float | None = None, extended_hours: bool = False, client=None) -> dict:
    """Sendet eine (Paper-)Kauforder (long). SL/TP managt der Bot, daher kein Bracket.

    - `notional` (USD): Market-DAY-Order über genau diesen Betrag → Bruchteile möglich
      (nur zur regulären Börsenzeit).
    - `qty` + `limit_price` + `extended_hours=True`: ganze Aktien als Limit-DAY (Pre-/After-Market).
    Robust: gibt {ok, id, detail} zurück, wirft nie."""
    client = _get_client(client)
    if client is None:
        return {"ok": False, "detail": "Alpaca nicht aktiv."}
    try:
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        if extended_hours:
            if not (qty and limit_price):
                return {"ok": False, "detail": "Extended Hours benötigt qty + limit_price."}
            req = LimitOrderRequest(
                symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
                limit_price=round(float(limit_price), 2), extended_hours=True)
            human = f"{symbol} ×{qty:g} Limit/Ext"
        else:
            kwargs = dict(symbol=symbol, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
            if notional is not None:
                kwargs["notional"] = round(float(notional), 2)
                human = f"{symbol} ${kwargs['notional']:.2f} (Bruchteile)"
            elif qty is not None:
                kwargs["qty"] = qty
                human = f"{symbol} ×{qty:g}"
            else:
                return {"ok": False, "detail": "Weder notional noch qty angegeben."}
            req = MarketOrderRequest(**kwargs)

        order = client.submit_order(req)
        log.info(f"Alpaca-Order {symbol} ({human}) ext={extended_hours} → id={getattr(order, 'id', '?')}")
        return {"ok": True, "id": str(getattr(order, "id", "")), "detail": human}
    except Exception as e:
        log.warning(f"Alpaca-Order {symbol} fehlgeschlagen: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def get_order_status(order_id: str, client=None) -> dict:
    """Aktueller Status einer Order — für die Fill-Bestätigung.
    status ist klein geschrieben, z. B. 'filled', 'accepted', 'pending_new', 'rejected'."""
    client = _get_client(client)
    if client is None:
        return {"ok": False, "status": "unknown", "detail": "Alpaca nicht aktiv."}
    try:
        o = client.get_order_by_id(order_id)
        raw = getattr(o, "status", "")
        status = str(getattr(raw, "value", None) or str(raw)).split(".")[-1].lower()
        return {"ok": True, "status": status,
                "filled_qty": float(getattr(o, "filled_qty", 0) or 0),
                "filled_avg_price": float(getattr(o, "filled_avg_price", 0) or 0)}
    except Exception as e:
        return {"ok": False, "status": "unknown", "detail": f"{type(e).__name__}: {e}"}


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
    """Schließt die offene Position zum Ticker (Market). `closed=False` (aber ok=True),
    wenn gar keine Position offen ist — dann ist nichts zu tun."""
    client = _get_client(client)
    if client is None:
        return {"ok": False, "closed": False, "detail": "Alpaca nicht aktiv."}
    try:
        order = client.close_position(symbol)
        return {"ok": True, "closed": True, "id": str(getattr(order, "id", "")),
                "detail": f"{symbol} geschlossen"}
    except Exception as e:
        msg = str(e).lower()
        if "position does not exist" in msg or "not found" in msg or "404" in msg:
            return {"ok": True, "closed": False, "detail": f"keine offene {symbol}-Position"}
        return {"ok": False, "closed": False, "detail": f"{type(e).__name__}: {e}"}
