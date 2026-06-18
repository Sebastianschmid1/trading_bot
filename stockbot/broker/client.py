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
        acct = account_summary(client)
        if not acct.get("ok"):
            return acct
        clock = client.get_clock()
        return {
            **acct,
            "market_open": bool(getattr(clock, "is_open", False)),
            "next_open": str(getattr(clock, "next_open", "")),
            "next_close": str(getattr(clock, "next_close", "")),
        }
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def account_summary(client=None) -> dict:
    """Kompakter Konto-Snapshot ohne Markt-Clock; geeignet für Order-Vorprüfungen."""
    client = _get_client(client)
    if client is None:
        return {"ok": False, "detail": "Alpaca nicht aktiv."}
    try:
        acct = client.get_account()
        return {
            "ok": True,
            "paper": config.ALPACA_PAPER,
            "status": str(getattr(acct, "status", "")),
            "cash": float(getattr(acct, "cash", 0) or 0),
            "buying_power": float(getattr(acct, "buying_power", 0) or 0),
            "currency": str(getattr(acct, "currency", "USD")),
            "detail": "OK",
        }
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def get_asset_info(symbol: str, client=None) -> dict:
    """Fragt bei Alpaca, ob ein Symbol als Asset handelbar ist (Aktie oder ETF).

    Rückgabe: {"ok": True, "tradable": bool, "asset_class": str, "symbol": str}
    oder {"ok": False, "detail": …} (Alpaca aus / Symbol unbekannt / Fehler). Wirft nie."""
    client = _get_client(client)
    if client is None:
        return {"ok": False, "detail": "Alpaca nicht aktiv."}
    try:
        asset = client.get_asset(symbol)
        return {
            "ok": True,
            "symbol": str(getattr(asset, "symbol", symbol)),
            "tradable": bool(getattr(asset, "tradable", False)),
            "asset_class": str(getattr(asset, "asset_class", "")),
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
        out = []
        for p in client.get_all_positions():
            out.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "side": str(getattr(p, "side", "")),
                "unrealized_pl": float(getattr(p, "unrealized_pl", 0) or 0),
            })
        return out
    except Exception as e:
        log.warning(f"Alpaca-Positionen nicht abrufbar: {e}")
        return []


def get_position(symbol: str, client=None) -> dict | None:
    """Einzelne offene Position zu einem Symbol, falls vorhanden."""
    client = _get_client(client)
    if client is None:
        return None
    try:
        p = client.get_open_position(symbol)
        return {
            "symbol": str(getattr(p, "symbol", symbol)),
            "qty": float(getattr(p, "qty", 0) or 0),
            "avg_entry": float(getattr(p, "avg_entry_price", 0) or 0),
            "side": str(getattr(p, "side", "")),
            "unrealized_pl": float(getattr(p, "unrealized_pl", 0) or 0),
        }
    except Exception as e:
        msg = str(e).lower()
        if "position does not exist" in msg or "not found" in msg or "404" in msg:
            return None
        log.warning(f"Alpaca-Position {symbol} nicht abrufbar: {e}")
        return None


def cancel_order(order_id: str, client=None) -> dict:
    """Storniert eine offene Order (Best-effort)."""
    client = _get_client(client)
    if client is None:
        return {"ok": False, "canceled": False, "detail": "Alpaca nicht aktiv."}
    try:
        client.cancel_order_by_id(order_id)
        return {"ok": True, "canceled": True, "detail": f"order {order_id} canceled"}
    except Exception as e:
        msg = str(e).lower()
        if "not found" in msg or "404" in msg:
            return {"ok": True, "canceled": False, "detail": f"order {order_id} nicht gefunden"}
        return {"ok": False, "canceled": False, "detail": f"{type(e).__name__}: {e}"}


def submit_exit_order(symbol: str, *, side: str, qty: float, limit_price: float | None = None,
                      extended_hours: bool = False, client=None) -> dict:
    """Sendet eine Exit-Order (SELL oder BUY-to-cover) als Market oder Limit."""
    client = _get_client(client)
    if client is None:
        return {"ok": False, "detail": "Alpaca nicht aktiv."}
    try:
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        side_u = str(side).upper()
        side_enum = OrderSide.SELL if side_u == "SELL" else OrderSide.BUY
        if limit_price is not None:
            req = LimitOrderRequest(
                symbol=symbol, qty=qty, side=side_enum, time_in_force=TimeInForce.DAY,
                limit_price=round(float(limit_price), 2), extended_hours=extended_hours,
            )
            human = f"{symbol} ×{qty:g} {side_u} Limit{'/Ext' if extended_hours else ''}"
        else:
            req = MarketOrderRequest(symbol=symbol, qty=qty, side=side_enum, time_in_force=TimeInForce.DAY)
            human = f"{symbol} ×{qty:g} {side_u} Market"
        order = client.submit_order(req)
        log.info(f"Alpaca-ExitOrder {symbol} ({human}) → id={getattr(order, 'id', '?')}")
        return {"ok": True, "id": str(getattr(order, "id", "")), "detail": human}
    except Exception as e:
        log.warning(f"Alpaca-ExitOrder {symbol} fehlgeschlagen: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


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


# ── Options (Long-Calls als gehebelte Trades) ────────────────────────────────

def _option_data_client(api_key: str | None = None, api_secret: str | None = None):
    """OptionHistoricalDataClient aus expliziten Keys (pro Nutzer) oder den .env-Keys.
    Gibt None zurück, wenn Keys/Paket fehlen — wirft nie."""
    key = api_key or config.ALPACA_API_KEY
    sec = api_secret or config.ALPACA_API_SECRET
    if not (key and sec):
        return None
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        return OptionHistoricalDataClient(key, sec)
    except Exception as e:
        log.warning(f"Options-Datenclient nicht verfügbar: {e}")
        return None


def list_option_contracts(underlying: str, *, dte_min: int, dte_max: int,
                          opt_type: str = "call", client=None) -> list[dict]:
    """Aktive Optionskontrakte zum Underlying im Verfallsfenster [heute+dte_min, heute+dte_max].
    Rückgabe: [{"symbol", "strike", "expiry"}]; [] bei Fehler/keine Daten. Wirft nie."""
    client = _get_client(client)
    if client is None:
        return []
    try:
        from datetime import date, timedelta
        from alpaca.trading.requests import GetOptionContractsRequest
        from alpaca.trading.enums import ContractType, AssetStatus
        today = date.today()
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=today + timedelta(days=dte_min),
            expiration_date_lte=today + timedelta(days=dte_max),
            type=ContractType.CALL if opt_type == "call" else ContractType.PUT,
            limit=1000,
        )
        resp = client.get_option_contracts(req)
        return [{"symbol": c.symbol, "strike": float(c.strike_price),
                 "expiry": str(c.expiration_date)}
                for c in (getattr(resp, "option_contracts", None) or [])]
    except Exception as e:
        log.warning(f"Optionskontrakte {underlying} nicht abrufbar: {e}")
        return []


def get_option_snapshot(symbol: str, *, api_key: str | None = None,
                        api_secret: str | None = None) -> dict:
    """Aktueller Snapshot eines Optionskontrakts: Mid-Prämie (aus Bid/Ask), Delta, IV.
    Rückgabe: {"ok": True, "premium", "bid", "ask", "delta", "iv"} oder {"ok": False, ...}."""
    dc = _option_data_client(api_key, api_secret)
    if dc is None:
        return {"ok": False, "detail": "Keine Options-Marktdaten (Keys/Paket fehlen)."}
    try:
        from alpaca.data.requests import OptionSnapshotRequest
        snaps = dc.get_option_snapshot(OptionSnapshotRequest(symbol_or_symbols=symbol))
        s = snaps.get(symbol) if isinstance(snaps, dict) else None
        if s is None:
            return {"ok": False, "detail": "kein Snapshot"}
        q = getattr(s, "latest_quote", None)
        bid = float(getattr(q, "bid_price", 0) or 0) if q else 0.0
        ask = float(getattr(q, "ask_price", 0) or 0) if q else 0.0
        mid = (bid + ask) / 2 if (bid and ask) else (ask or bid)
        greeks = getattr(s, "greeks", None)
        delta = float(getattr(greeks, "delta", 0) or 0) if greeks else None
        iv = float(getattr(s, "implied_volatility", 0) or 0) or None
        return {"ok": True, "premium": mid, "bid": bid, "ask": ask, "delta": delta, "iv": iv}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def submit_option_buy(option_symbol: str, qty: int, client=None) -> dict:
    """Kauft `qty` Optionskontrakte (Market-DAY, long). Nur ganze Kontrakte, kein Notional.
    Rückgabe {ok, id, detail}; wirft nie."""
    client = _get_client(client)
    if client is None:
        return {"ok": False, "detail": "Alpaca nicht aktiv."}
    if not qty or qty < 1:
        return {"ok": False, "detail": "qty < 1 Kontrakt."}
    try:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req = MarketOrderRequest(symbol=option_symbol, qty=int(qty),
                                 side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        order = client.submit_order(req)
        human = f"{option_symbol} ×{int(qty)} Kontrakt(e)"
        log.info(f"Alpaca-Options-Order {human} → id={getattr(order, 'id', '?')}")
        return {"ok": True, "id": str(getattr(order, "id", "")), "detail": human}
    except Exception as e:
        log.warning(f"Alpaca-Options-Order {option_symbol} fehlgeschlagen: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def select_option_for_leverage(underlying: str, price: float, target_leverage: float,
                               budget: float, *, client=None, api_key: str | None = None,
                               api_secret: str | None = None) -> dict | None:
    """Wählt einen Long-Call, dessen effektiver Hebel (Omega ≈ |delta|×Kurs/Prämie) am nächsten
    am Ziel-Hebel liegt und der mind. 1× ins Budget passt (Prämie×100 ≤ Budget).

    Rückgabe: {option_symbol, strike, expiry, premium, delta, omega, qty} oder None
    (keine Kette/keine Daten/zu teuer). Macht bis zu 25 Snapshot-Abrufe (strikenah zuerst)."""
    if not price or price <= 0 or not budget or budget <= 0:
        return None
    contracts = list_option_contracts(
        underlying, dte_min=config.OPTION_TARGET_DTE_MIN,
        dte_max=config.OPTION_TARGET_DTE_MAX, opt_type=config.OPTION_TYPE, client=client)
    if not contracts:
        return None
    contracts.sort(key=lambda c: abs(c["strike"] - price))   # near-the-money zuerst
    best = None
    for c in contracts[:25]:
        snap = get_option_snapshot(c["symbol"], api_key=api_key, api_secret=api_secret)
        premium, delta = snap.get("premium"), snap.get("delta")
        if not snap.get("ok") or not premium or premium <= 0 or not delta:
            continue
        cost = premium * 100
        if cost > budget:                      # nicht mal 1 Kontrakt bezahlbar
            continue
        omega = abs(delta) * price / premium
        score = abs(omega - target_leverage)
        if best is None or score < best["_score"]:
            best = {"option_symbol": c["symbol"], "strike": c["strike"], "expiry": c["expiry"],
                    "premium": round(premium, 4), "delta": round(delta, 4),
                    "omega": round(omega, 2), "qty": int(budget // cost), "_score": score}
    if best:
        best.pop("_score", None)
    return best
