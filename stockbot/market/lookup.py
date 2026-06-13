"""
Symbol-Validierung & -Suche für die persönliche Watchlist (reines yfinance).

- `validate_ticker(symbol)`  → prüft per fast_info, ob ein Symbol existiert/Kursdaten hat
  (gilt für Aktien UND ETFs), liefert Name + Typ + Kurs.
- `search_symbols(query)`    → yfinance-Symbolsuche für die "Meinten Sie?"-Hilfe.

Beide Funktionen sind blockierend (Netz) und werfen NIE — Fehler ⇒ {"ok": False} bzw. [].
Im Bot in asyncio.to_thread aufrufen. Wirklich keine Projektabhängigkeiten (kein Import-Zyklus).
"""

import logging

import yfinance as yf

log = logging.getLogger(__name__)


def _last_price(sym: str) -> float | None:
    """Letzter verfügbarer Kurs eines Symbols. Robust auch bei geschlossener Börse (Wochenende):
    erst fast_info (schnell), sonst der letzte Schlusskurs aus der Historie."""
    try:
        p = yf.Ticker(sym).fast_info.last_price
        if p is not None:
            return float(p)
    except Exception:
        pass
    try:
        closes = yf.Ticker(sym).history(period="5d")["Close"].dropna()
        if len(closes):
            return float(closes.iloc[-1])
    except Exception as e:
        log.warning(f"Kurs-Historie für {sym} nicht abrufbar: {e}")
    return None


def validate_ticker(symbol: str) -> dict:
    """Prüft, ob `symbol` an der Börse existiert und Kursdaten hat.

    Rückgabe:
      {"ok": True,  "symbol", "name", "quote_type" (EQUITY/ETF/…), "price"}  oder
      {"ok": False, "symbol"}  wenn kein Kurs abrufbar ist.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"ok": False, "symbol": sym}

    price = _last_price(sym)
    if price is None:
        return {"ok": False, "symbol": sym}

    # Name/Typ sind "nice to have" — Ausfall darf die Validierung nicht kippen.
    name, quote_type = sym, "EQUITY"
    try:
        info = yf.Ticker(sym).info or {}
        name = info.get("shortName") or info.get("longName") or sym
        quote_type = (info.get("quoteType") or "EQUITY").upper()
    except Exception:
        pass

    return {"ok": True, "symbol": sym, "name": name,
            "quote_type": quote_type, "price": float(price)}


def search_symbols(query: str, limit: int = 5) -> list[dict]:
    """yfinance-Symbolsuche → [{"symbol","name","quote_type"}]. Bei Fehler/fehlender API: []."""
    q = (query or "").strip()
    if not q:
        return []
    try:
        quotes = yf.Search(q, max_results=limit).quotes or []
    except Exception as e:
        log.warning(f"Symbolsuche fehlgeschlagen für '{q}': {e}")
        return []

    out = []
    for it in quotes:
        sym = (it.get("symbol") or "").strip().upper()
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "name": it.get("shortname") or it.get("longname") or sym,
            "quote_type": (it.get("quoteType") or "").upper(),
        })
        if len(out) >= limit:
            break
    return out
