"""Planbarer Polymarket-Erhebungszyklus (PM-0, siehe docs/POLYMARKET_INTEGRATION.md §4).

Bündelt Poll (IO, ``polymarket.py``) → Qualität (rein, ``polymarket_quality.py``) →
Persistenz (DB-Seam + Rohdatenarchiv) zu einer Zyklusfunktion. **Bewusst noch nicht
registriert** — kein ``run_repeating`` in ``tgbot/bot.py`` (PM-0-Scope: der Bot verhält sich
nach diesem Modul exakt wie vorher). Das Wiring folgt frühestens in PM-1, nach der manuell
freigegebenen Mapping-Datei (Nutzerentscheidung).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from stockbot.core import db
from stockbot.research import polymarket, polymarket_quality

log = logging.getLogger(__name__)

# Δp-Berechnung braucht höchstens die letzten 24h Snapshot-Historie (größtes Fenster).
_HISTORY_WINDOW = timedelta(hours=24)


def _parse_end_date(value: str | None) -> datetime | None:
    """Best-effort-Parsing von Gammas ``endDate`` (ISO-8601). Unparsbare/fehlende Werte
    ⇒ ``None`` — die Restlaufzeit-Prüfung wird dann übersprungen statt zu raten."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _process_market(
    market: dict, *, thresholds: polymarket_quality.QualityThresholds,
) -> int:
    """Ein Markt: Stammdaten upserten, Snapshot holen, archivieren, bewerten, persistieren.
    Gibt 1 zurück, wenn eine Snapshot-Zeile geschrieben wurde, sonst 0 (kein
    ``condition_id`` auflösbar)."""
    info = polymarket.parse_market_info(market)
    if not info.condition_id:
        log.warning("Polymarket-Zyklus: Markt ohne condition_id übersprungen.")
        return 0
    db.upsert_polymarket_market(info)

    snapshot = polymarket.fetch_market_snapshot(market)
    raw_path = polymarket.write_raw_snapshot(
        info.condition_id, snapshot.fetched_at, snapshot.raw)

    history = db.polymarket_snapshot_history(
        info.condition_id, since=snapshot.fetched_at - _HISTORY_WINDOW)
    end_date = _parse_end_date(info.end_date)

    state = polymarket_quality.evaluate_snapshot(
        bid=snapshot.bid, ask=snapshot.ask,
        depth_bid_usd=snapshot.depth_bid_usd, depth_ask_usd=snapshot.depth_ask_usd,
        volume_24h_usd=snapshot.volume_24h_usd, trade_count_24h=snapshot.trade_count_24h,
        fetched_at=snapshot.fetched_at, end_date=end_date,
        history=history, thresholds=thresholds)

    spread_bps = polymarket_quality.compute_spread_bps(snapshot.bid, snapshot.ask)
    db.record_polymarket_snapshot(
        snapshot, mid=state.probability, spread_bps=spread_bps,
        usable=state.usable, reject_code=state.reject_code, raw_file_path=raw_path)
    return 1


def run_polymarket_cycle(
    markets: Iterable[dict] | None = None, *,
    thresholds: polymarket_quality.QualityThresholds = polymarket_quality.DEFAULT_THRESHOLDS,
) -> int:
    """Ein Erhebungszyklus über alle beobachteten Märkte. ``markets`` ist injizierbar
    (Tests, spätere Mapping-Filterung in PM-1); ohne Angabe holt der Zyklus die aktive
    Marktliste über ``polymarket.fetch_markets()``.

    Fail-neutral auf zwei Ebenen: schlägt die Marktdiscovery komplett fehl, liefert der
    Zyklus 0 (kein Crash). Scheitert EIN Markt (Netzwerk, unerwartetes Antwortformat), wird
    er übersprungen und geloggt — die übrigen Märkte laufen weiter. Gibt die Anzahl
    gespeicherter Snapshots zurück."""
    if markets is not None:
        market_list = list(markets)
    else:
        try:
            market_list = polymarket.fetch_markets()
        except polymarket.PolymarketAPIError as exc:
            log.warning(f"Polymarket-Zyklus: Marktdiscovery fehlgeschlagen ({exc}) — 0 Snapshots.")
            return 0

    stored = 0
    for market in market_list:
        try:
            stored += _process_market(market, thresholds=thresholds)
        except Exception as exc:   # ein einzelner Markt darf den Zyklus nie kippen
            log.warning(f"Polymarket-Zyklus: Markt übersprungen ({exc}).")
    return stored
