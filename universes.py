"""
Aktien-Universen: lädt die Index-Bestandteile automatisch (mit Cache & Fallback).

- S&P 500: vollständige Liste live von Wikipedia (~500 Werte, alle US-gelistet → yfinance-kompatibel).
- MSCI World / Emerging Markets: es gibt keine verlässliche, frei abrufbare Vollliste, die sauber
  auf yfinance-Symbole passt (ausländische Börsen, Währungen, Performance). Daher werden hier die
  kuratierten Körbe aus config.py als Fallback genutzt.

Ergebnis wird in data/universes_cache.json zwischengespeichert und nur wöchentlich neu geladen.
Schlägt das Laden fehl (offline o. Ä.), greift automatisch der Fallback-Korb.
"""

import json
import time
import logging
from io import StringIO
from pathlib import Path

import requests
import pandas as pd

import config

log = logging.getLogger(__name__)

CACHE_FILE = Path("data/universes_cache.json")
CACHE_MAX_AGE_S = 7 * 24 * 3600      # 1 Woche
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockSignalBot/1.0)"}

# Kuratierte Fallback-Körbe (aus config.py)
FALLBACK = {
    "sp500":      config.UNIVERSE_SP500,
    "msci_world": config.UNIVERSE_MSCI_WORLD,
    "emerging":   config.UNIVERSE_EMERGING,
}


# ── Auto-Quellen ─────────────────────────────────────────────────────────────

def fetch_sp500() -> list[str]:
    """Lädt die aktuelle S&P-500-Liste von Wikipedia und normalisiert sie für yfinance."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    r = requests.get(url, headers=_HEADERS, timeout=20)
    r.raise_for_status()
    df = pd.read_html(StringIO(r.text))[0]
    syms = [str(s).replace(".", "-").strip() for s in df["Symbol"].tolist()]   # BRK.B → BRK-B
    syms = [s for s in syms if s and s.lower() != "nan"]
    return sorted(set(syms))


# Nur Bereiche mit verlässlicher Auto-Quelle bekommen einen Fetcher.
FETCHERS = {
    "sp500": fetch_sp500,
}


# ── Cache ────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict):
    try:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except Exception as e:
        log.warning(f"Universen-Cache konnte nicht gespeichert werden: {e}")


# ── Öffentliche API ──────────────────────────────────────────────────────────

def get_tickers(region: str, max_age_s: int = CACHE_MAX_AGE_S, force: bool = False) -> list[str]:
    """
    Gibt die Ticker eines Bereichs zurück. Reihenfolge:
      1. frischer Cache (jünger als max_age_s)
      2. Auto-Quelle (falls vorhanden) → in Cache schreiben
      3. alter Cache (falls Auto-Quelle fehlschlägt)
      4. kuratierter Fallback-Korb aus config.py
    """
    fetcher = FETCHERS.get(region)
    if fetcher is None:
        return list(FALLBACK.get(region, []))   # kein Auto-Quell → Korb

    cache = _load_cache()
    entry = cache.get(region)
    if entry and entry.get("tickers") and not force and (time.time() - entry.get("fetched_at", 0) < max_age_s):
        return entry["tickers"]

    try:
        tickers = fetcher()
        if not tickers:
            raise ValueError("leere Liste erhalten")
        cache[region] = {"fetched_at": time.time(), "tickers": tickers}
        _save_cache(cache)
        log.info(f"Universum '{region}': {len(tickers)} Ticker automatisch geladen.")
        return tickers
    except Exception as e:
        log.warning(f"Auto-Laden für '{region}' fehlgeschlagen ({e}) — nutze Fallback.")
        if entry and entry.get("tickers"):
            return entry["tickers"]
        return list(FALLBACK.get(region, []))
