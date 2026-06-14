"""
Asset-Klassen-Registry für die Web-App (On-Demand-Signale, Dropdown).

Eine Asset-Klasse bündelt:
  - das **Universum** (welche Ticker analysiert werden) — `get_tickers(user)`,
  - ein **Profil** (instrument-spezifische Analyse-Parameter) — `Profile`,
  - die **Signal-Funktion** (Default: analyzer.analyze_ticker mit dem Profil).

So werden ETFs/Krypto/Rohstoffe (spätere Schritte) zu reinen Registry-Einträgen, ohne die
Engine zu duplizieren. Heute ist nur "stocks" (Aktien) registriert — das Standard-Profil
entspricht exakt dem bisherigen Aktien-Verhalten (keine Verhaltensänderung für Bestand).
"""

from dataclasses import dataclass, field
from typing import Callable

from stockbot import config
from stockbot.market import universes


# ── Analyse-Profil (instrument-spezifische Parameter) ────────────────────────

@dataclass(frozen=True)
class Profile:
    """Analyse-Parameter, die je Asset-Klasse abweichen können. Defaults = Aktien
    (aus config.py) → für Bestandsnutzer ändert sich nichts."""
    prepost: bool = True                                   # Pre-/After-Market-Bars laden (nur Aktien sinnvoll)
    block_weekly_downtrend: bool = config.BLOCK_WEEKLY_DOWNTREND
    rsi_oversold: float = config.RSI_OVERSOLD
    rsi_overbought: float = config.RSI_OVERBOUGHT
    min_strength: float = config.MIN_SIGNAL_STRENGTH
    smart_money: bool = True                               # Insider/13F-Re-Ranking anwendbar?


STOCK_PROFILE = Profile()

# ETFs: gleiche Engine, aber kein Smart-Money (Insider/13F gibt es für Körbe nicht);
# etwas niedrigere Mindeststärke, da ETFs weniger volatil sind.
ETF_PROFILE = Profile(smart_money=False, min_strength=max(50.0, config.MIN_SIGNAL_STRENGTH - 5))

# Rohstoffe werden über Rohstoff-ETFs gehandelt → identisches Profil wie ETFs.
COMMODITY_PROFILE = ETF_PROFILE

# Krypto: 24/7-Handel → kein Pre/After-Market, kein (Börsen-)Wochentrend-Filter, kein
# Smart-Money; volatiler → weitere RSI-Schwellen.
CRYPTO_PROFILE = Profile(
    prepost=False, block_weekly_downtrend=False, smart_money=False,
    rsi_oversold=30.0, rsi_overbought=75.0,
)


# ── Asset-Klasse ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AssetClass:
    key: str
    label: str
    profile: Profile
    get_tickers: Callable[[dict], list]                    # (user) -> list[str]
    description: str = ""


def _user_regions(user: dict) -> list[str]:
    """Aktive Markt-Körbe des Nutzers (Schlüssel aus config.UNIVERSES, mind. einer)."""
    keys = (user or {}).get("market_regions") or [(user or {}).get("market_region") or config.DEFAULT_REGION]
    return [k for k in keys if k in config.UNIVERSES] or [config.DEFAULT_REGION]


def _stock_tickers(user: dict) -> list[str]:
    """Aktien-Universum des Nutzers: alle gewählten Regionen, dedupliziert.
    Respektiert den Voll-Universum-Schalter (auto_universe)."""
    auto = (user or {}).get("auto_universe", config.DEFAULT_AUTO_UNIVERSE)
    seen: dict[str, None] = {}
    for region in _user_regions(user):
        for t in universes.get_tickers(region, auto=auto):
            seen.setdefault(t, None)
    return list(seen)


def _fixed(tickers: list[str]):
    """Erzeugt eine get_tickers-Funktion mit fester Liste (ETF/Rohstoff/Krypto)."""
    return lambda user: list(tickers)


# ── Registry ─────────────────────────────────────────────────────────────────

ASSET_CLASSES: dict[str, AssetClass] = {
    "stocks": AssetClass(
        key="stocks", label="Aktien", profile=STOCK_PROFILE,
        get_tickers=_stock_tickers,
        description="US-/ADR-Aktien aus den gewählten Markt-Körben.",
    ),
    "etf": AssetClass(
        key="etf", label="ETFs", profile=ETF_PROFILE,
        get_tickers=_fixed(config.UNIVERSE_ETF),
        description="Liquide ETFs (Indizes, Sektoren, Themen) — bei Alpaca handelbar.",
    ),
    "crypto": AssetClass(
        key="crypto", label="Krypto", profile=CRYPTO_PROFILE,
        get_tickers=_fixed(config.UNIVERSE_CRYPTO),
        description="Kryptowährungen (yfinance, 24/7). Vorerst Demo/Tracking auf der Website.",
    ),
    "commodity": AssetClass(
        key="commodity", label="Rohstoffe", profile=COMMODITY_PROFILE,
        get_tickers=_fixed(config.UNIVERSE_COMMODITY),
        description="Rohstoffe über handelbare Rohstoff-ETFs (Gold, Öl, Gas, …).",
    ),
}

DEFAULT_ASSET = "stocks"


def get_asset_class(key: str | None) -> AssetClass:
    """Liefert die Asset-Klasse zum Schlüssel (Fallback: Aktien)."""
    return ASSET_CLASSES.get(key or "", ASSET_CLASSES[DEFAULT_ASSET])


# Schnelle Zuordnung Ticker → Klasse (für den Filter aktiver Trades).
_TICKER_CLASS = {}
for _k, _lst in (("crypto", config.UNIVERSE_CRYPTO), ("commodity", config.UNIVERSE_COMMODITY),
                 ("etf", config.UNIVERSE_ETF)):
    for _t in _lst:
        _TICKER_CLASS.setdefault(_t.upper(), _k)


def classify_ticker(ticker: str) -> str:
    """Ordnet einen Ticker einer Anlageklasse zu (crypto/commodity/etf, sonst 'stocks').
    Krypto erkennt zusätzlich am '-USD'-Suffix (yfinance), falls nicht in der Liste."""
    t = (ticker or "").upper()
    if t in _TICKER_CLASS:
        return _TICKER_CLASS[t]
    if t.endswith("-USD"):
        return "crypto"
    return "stocks"


def all_asset_classes() -> list[AssetClass]:
    """Alle registrierten Asset-Klassen in Anzeige-Reihenfolge (Aktien zuerst)."""
    return list(ASSET_CLASSES.values())
