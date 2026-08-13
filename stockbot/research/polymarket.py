"""Polymarket Read-Only-Client (PM-0, Research-Tier, siehe docs/POLYMARKET_INTEGRATION.md §4).

Holt Marktdiscovery, Orderbuch und Trades ausschließlich über öffentliche, unauthentifizierte
Endpunkte — kein Wallet, kein Private Key, kein Polymarket-API-Key, kein Order-/Signing-
Endpunkt (Nicht-Ziel aus dem Plan). ``requests`` ist bereits Projekt-Dependency (siehe
``stockbot/market/universes.py``); keine neue HTTP-Dependency, insbesondere kein
``py-clob-client``.

**Endpunkt-Hinweis (Umsetzer, 2026-08-13):** Die Pfade unten stammen aus Trainingswissen des
Umsetzers, NICHT aus einem Live-Abgleich mit ``docs.polymarket.com`` — die Sandbox-Egress-
Policy blockt sowohl ``docs.polymarket.com`` als auch ``gamma-api.polymarket.com``
(``EGRESS_BLOCKED``, per WebFetch und curl geprüft). Vor dem ersten Smoke-Lauf gegen die
echte API MUSS ein Mensch mit Netzzugriff diese Pfade gegen die aktuelle Dokumentation
gegenprüfen. Das ist im Handoff-Report offen ausgewiesen, nicht stillschweigend angenommen.

Polling statt WebSocket ist bewusst (Simplicity-Entscheidung, kein Versehen): Stunden-/Tages-
Ereignisse brauchen keine Streaming-Latenz. Keine Endlos-Retries — genau ein Retry bei
Ratelimit/Server-Fehler, danach wird der Fehler weitergereicht; der Scheduler fängt ihn
fail-neutral ab (siehe ``polymarket_scheduler.py``).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from stockbot.paths import DATA_DIR

log = logging.getLogger(__name__)

# Basis-URLs der drei öffentlichen Polymarket-APIs (siehe Endpunkt-Hinweis oben).
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"

DEFAULT_TIMEOUT = (5.0, 10.0)   # (connect, read) Sekunden — nie unbegrenzt warten
_HEADERS = {"User-Agent": "stockbot-research/1.0 (+read-only polymarket sensor)"}
_MAX_RETRIES = 1                 # genau ein Retry, keine Endlosschleife bei Ratelimit/5xx
_RETRY_BACKOFF_S = 2.0
_MAX_RETRY_WAIT_S = 30.0

POLYMARKET_ARCHIVE_DIR = DATA_DIR / "polymarket_archive"


class PolymarketAPIError(RuntimeError):
    """Der Client konnte keine gültige Antwort holen (Netzwerk, 4xx/5xx, Ratelimit)."""


# ── Wertobjekte ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketInfo:
    """Stammdaten eines Polymarket-Markts — ändert sich fast nie (Tabelle
    ``polymarket_markets``). ``resolution_rules`` ist der Volltext der Auflösungsregel; der
    Wortlaut ist die eigentliche Semantik (Plan §3)."""
    condition_id: str
    token_id: str
    slug: str
    question: str
    resolution_source: str
    resolution_rules: str
    end_date: str | None
    category: str | None


@dataclass(frozen=True)
class MarketSnapshot:
    """Ein Abrufzeitpunkt, noch unbewertet — Eingabe für ``polymarket_quality.py``. ``raw``
    ist der unveränderte, kombinierte JSON-Response (Markt + Buch + Trades) fürs
    Rohdatenarchiv, inkl. ``question``/``resolution_rules``."""
    condition_id: str
    fetched_at: datetime
    bid: float | None
    ask: float | None
    depth_bid_usd: float | None
    depth_ask_usd: float | None
    volume_24h_usd: float | None
    open_interest_usd: float | None
    trade_count_24h: int | None
    last_trade_at: datetime | None
    raw: dict[str, Any]


# ── HTTP-Basis ────────────────────────────────────────────────────────────────

def _get(
    base_url: str, path: str, params: dict | None = None, *,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
) -> Any:
    """GET mit explizitem Timeout und genau einem Retry bei Ratelimit (429) / Server-Fehler
    (5xx). Bewusst KEINE Endlosschleife — schlägt auch der Retry fehl, wird
    ``PolymarketAPIError`` geworfen; der Aufrufer (Scheduler) fängt sie fail-neutral ab."""
    url = f"{base_url}{path}"
    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_S)
                continue
            raise PolymarketAPIError(f"GET {url} fehlgeschlagen: {exc}") from exc

        if response.status_code == 429 or response.status_code >= 500:
            last_error = PolymarketAPIError(f"GET {url} -> HTTP {response.status_code}")
            if attempt < _MAX_RETRIES:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else _RETRY_BACKOFF_S
                except ValueError:
                    wait = _RETRY_BACKOFF_S
                time.sleep(min(wait, _MAX_RETRY_WAIT_S))
                continue
            raise last_error

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise PolymarketAPIError(f"GET {url} -> HTTP {response.status_code}") from exc
        return response.json()

    raise PolymarketAPIError(f"GET {url} fehlgeschlagen: {last_error}")


# ── Marktdiscovery (Gamma API) ─────────────────────────────────────────────────

def fetch_markets(*, active: bool = True, closed: bool = False,
                  limit: int = 100, offset: int = 0) -> list[dict]:
    """Marktdiscovery über die Gamma API: ``GET /markets``."""
    params = {"active": str(active).lower(), "closed": str(closed).lower(),
              "limit": limit, "offset": offset}
    data = _get(GAMMA_API_BASE, "/markets", params)
    return data if isinstance(data, list) else list(data.get("data", []))


def _first_token_id(market: dict) -> str:
    """Gamma liefert ``clobTokenIds`` häufig als JSON-kodierten String (z. B.
    ``'["123", "456"]'``) statt als Liste — robust gegen beide Formen parsen. Erster Eintrag
    ist konventionell der 'Yes'-Outcome-Token."""
    raw = market.get("clobTokenIds", market.get("clob_token_ids", market.get("tokens")))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = []
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict):
            return str(first.get("token_id", first.get("tokenId", "")))
        return str(first)
    return ""


def parse_market_info(market: dict) -> MarketInfo:
    """Extrahiert die Stammdaten-Felder aus einem rohen Gamma-``market``-Objekt. Fehlende
    Felder werden defensiv zu leeren Strings/``None`` — kein KeyError bei einem unerwarteten
    Antwortformat."""
    return MarketInfo(
        condition_id=str(market.get("conditionId", market.get("condition_id", ""))),
        token_id=_first_token_id(market),
        slug=str(market.get("slug", "")),
        question=str(market.get("question", "")),
        resolution_source=str(market.get("resolutionSource", market.get("resolution_source", ""))),
        resolution_rules=str(market.get("description", market.get("resolution_rules", ""))),
        end_date=market.get("endDate", market.get("end_date")),
        category=market.get("category"),
    )


# ── Orderbuch / Midpoint / Preisverlauf (CLOB API) ─────────────────────────────

def fetch_book(token_id: str) -> dict:
    """Orderbuch (Bid-/Ask-Stufen) über die CLOB API: ``GET /book``."""
    return _get(CLOB_API_BASE, "/book", {"token_id": token_id})


def fetch_midpoint(token_id: str) -> dict:
    """Mittelkurs über die CLOB API: ``GET /midpoint`` (Referenz, falls das Buch dünn ist;
    die eigentliche Bewertung nutzt ``polymarket_quality.compute_mid`` aus Bid/Ask)."""
    return _get(CLOB_API_BASE, "/midpoint", {"token_id": token_id})


def fetch_price_history(token_id: str, *, interval: str = "1d", fidelity: int = 60) -> dict:
    """Preisverlauf über die CLOB API: ``GET /prices-history`` — Rohmaterial für spätere,
    feinere Δp-Analysen; PM-0 berechnet Δp aus der eigenen Snapshot-Historie."""
    return _get(CLOB_API_BASE, "/prices-history",
               {"market": token_id, "interval": interval, "fidelity": fidelity})


# ── Trades / Volumen (Data API) ─────────────────────────────────────────────────

def fetch_trades(condition_id: str, *, limit: int = 100) -> list[dict]:
    """Letzte Trades über die Data API: ``GET /trades`` — Grundlage für 24h-Volumen und
    Trade-Anzahl im Qualitätsfilter."""
    data = _get(DATA_API_BASE, "/trades", {"market": condition_id, "limit": limit})
    return data if isinstance(data, list) else list(data.get("data", []))


# ── Snapshot-Aufbau ──────────────────────────────────────────────────────────

def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _book_top(levels: Any) -> tuple[float | None, float | None]:
    """Bester Preis + USD-Tiefe der besten Buchstufe. Die CLOB liefert Stufen als
    ``[{"price": "...", "size": "..."}, ...]``; ein leeres oder fehlerhaftes Buch liefert
    ``(None, None)`` — kein erfundener Preis."""
    if not levels or not isinstance(levels, list):
        return None, None
    best = levels[0]
    if not isinstance(best, dict):
        return None, None
    price = _to_float(best.get("price"))
    size = _to_float(best.get("size"))
    if price is None or size is None:
        return None, None
    return price, price * size


def _trade_amount_usd(trade: dict) -> float:
    for key in ("size", "amount", "value", "usdcAmount"):
        amount = _to_float(trade.get(key))
        if amount is not None:
            return amount
    return 0.0


def _sum_trade_volume(trades: list[dict]) -> float | None:
    if not trades:
        return None
    return sum(_trade_amount_usd(trade) for trade in trades)


def _parse_trade_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _last_trade_at(trades: list[dict]) -> datetime | None:
    if not trades:
        return None
    timestamps = [t for t in (
        _parse_trade_time(trade.get("timestamp", trade.get("match_time", trade.get("time"))))
        for trade in trades
    ) if t is not None]
    return max(timestamps) if timestamps else None


def fetch_market_snapshot(market: dict, *, trades_limit: int = 100) -> MarketSnapshot:
    """Voller Abrufzyklus für EINEN Markt: Buch + Trades holen und zu einem unbewerteten
    Snapshot samt unverändertem Rohdaten-Bundle bündeln. Ein fehlgeschlagener Teil-Abruf
    (Buch ODER Trades) bricht den Snapshot nicht ab — die betroffenen Felder bleiben ``None``/
    leer und der Qualitätsfilter lehnt den Snapshot dann folgerichtig ab (dünnes Buch /
    kein Volumen), statt dass der ganze Zyklus abbricht."""
    info = parse_market_info(market)
    fetched_at = datetime.now(timezone.utc)

    book: dict = {}
    if info.token_id:
        try:
            book = fetch_book(info.token_id)
        except PolymarketAPIError as exc:
            log.warning(f"Polymarket-Buch für {info.condition_id} nicht abrufbar: {exc}")

    trades: list[dict] = []
    if info.condition_id:
        try:
            trades = fetch_trades(info.condition_id, limit=trades_limit)
        except PolymarketAPIError as exc:
            log.warning(f"Polymarket-Trades für {info.condition_id} nicht abrufbar: {exc}")

    bid, depth_bid = _book_top(book.get("bids") if isinstance(book, dict) else None)
    ask, depth_ask = _book_top(book.get("asks") if isinstance(book, dict) else None)

    return MarketSnapshot(
        condition_id=info.condition_id,
        fetched_at=fetched_at,
        bid=bid, ask=ask,
        depth_bid_usd=depth_bid, depth_ask_usd=depth_ask,
        volume_24h_usd=_sum_trade_volume(trades),
        open_interest_usd=_to_float(market.get("liquidity")),
        trade_count_24h=len(trades),
        last_trade_at=_last_trade_at(trades),
        raw={"market": market, "book": book, "trades": trades},
    )


# ── Rohdatenarchiv (JSON, Muster von core/raw_data_archive.py) ────────────────

def raw_json_path(
    condition_id: str, fetched_at: datetime, *, base_dir: Path = POLYMARKET_ARCHIVE_DIR,
) -> Path:
    """Partitionierter Pfad: ``<base_dir>/<condition_id>/<YYYY-MM-DD>/<HHMMSS>.json`` — Muster
    von ``core/raw_data_archive.parquet_path``, aber JSON statt Parquet und ohne Timeframe
    (ein Polymarket-Markt hat keinen Timeframe, dafür mehrere Abrufe pro Tag, daher die
    Uhrzeit statt eines fixen Dateinamens in der Partition)."""
    aware = fetched_at.astimezone(timezone.utc) if fetched_at.tzinfo else fetched_at
    safe_id = condition_id.strip() or "unknown"
    return base_dir / safe_id / aware.date().isoformat() / f"{aware.strftime('%H%M%S')}.json"


def write_raw_snapshot(
    condition_id: str, fetched_at: datetime, payload: dict, *,
    base_dir: Path = POLYMARKET_ARCHIVE_DIR,
) -> str:
    """Schreibt den unveränderten kombinierten JSON-Response (Markt inkl. ``question``/
    ``resolution_rules``, Buch, Trades) verlustfrei weg. Gibt den Dateipfad zurück — er wird
    als ``raw_file_path`` in die Snapshot-Zeile aufgenommen (die Metadatenzeile über den
    DB-Seam aus Plan §3; ein eigenes Metadaten-Tabellenpaar wie bei
    ``core/raw_data_archive`` entfällt, weil 1 Snapshot == 1 archivierte Datei ist)."""
    path = raw_json_path(condition_id, fetched_at, base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return str(path)
