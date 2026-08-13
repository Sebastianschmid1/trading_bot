"""Polymarket-Qualitäts-/Liquiditätsfilter (PM-0, siehe docs/POLYMARKET_INTEGRATION.md §3/§4).

Rein, IO-frei — analog zu ``stockbot/core/data_quality.py``/``risk_sizing.py``: ein
Entscheidungs-Dataclass statt Exceptions, damit Aufrufer den Ablehnungsgrund direkt anzeigen
können. Die Snapshot-Historie für die Δp-Berechnung wird als Parameter übergeben, nicht selbst
aus der DB geladen — dieses Modul importiert bewusst nichts aus ``core.db``.

Dünne Bücher, Einzeltrades und veraltete Daten ⇒ ``usable=False``. **PM-0 verdrahtet
``EventState`` nirgends in einen Signal-/Order-/Anzeigepfad** — das ist Vorbereitung für
PM-1/PM-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence


@dataclass(frozen=True)
class QualityThresholds:
    """Schwellenwerte des Liquiditäts-/Frischefilters — ein Ort für alle Defaults, damit
    Tests und spätere Kalibrierung nicht im Code verstreut suchen müssen."""
    min_depth_usd: float = 100.0
    max_spread_bps: float = 500.0
    min_volume_24h_usd: float = 500.0
    min_trade_count_24h: int = 5
    max_snapshot_age_seconds: float = 3600.0
    min_time_to_resolution_seconds: float = 24 * 3600.0


DEFAULT_THRESHOLDS = QualityThresholds()


@dataclass(frozen=True)
class EventState:
    """Bewertetes Ergebnis eines Polymarket-Snapshots. ``usable=False`` heißt: dieser
    Snapshot darf sich auf keinen Signal-/Risikopfad auswirken. ``delta`` enthält Δp über die
    Fenster ``1h``/``6h``/``24h`` (``None`` je Fenster, wenn kein älterer Snapshot in diesem
    Fenster vorliegt — keine erfundene Differenz)."""
    usable: bool
    probability: float | None
    delta: Mapping[str, float | None]
    reject_code: str
    reason: str = ""


_EMPTY_DELTA: Mapping[str, float | None] = {"1h": None, "6h": None, "24h": None}
_WINDOWS = {"1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24)}


def compute_mid(bid: float | None, ask: float | None) -> float | None:
    """Mittelkurs aus Bid/Ask. ``None`` wenn eine Seite fehlt — kein erfundener Mittelwert."""
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def compute_spread_bps(bid: float | None, ask: float | None) -> float | None:
    """Spread in Basispunkten relativ zum Bid — analog zu
    ``core/data_quality.py::check_spread``."""
    if bid is None or ask is None or bid <= 0:
        return None
    return (ask - bid) / bid * 10_000


def _ensure_aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _reject(code: str, reason: str, *, probability: float | None = None) -> EventState:
    return EventState(usable=False, probability=probability, delta=_EMPTY_DELTA,
                      reject_code=code, reason=reason)


def _compute_deltas(
    current_mid: float | None, history: Sequence[Mapping], *, now: datetime,
) -> dict[str, float | None]:
    """Für jedes Fenster (1h/6h/24h): „as-of"-Referenz — der JÜNGSTE Snapshot, der mindestens
    so alt ist wie das Fenster (``fetched_at <= now - Fenster``), liefert den Referenzwert für
    Δp = aktuell − Referenz (Standard-Zeitreihen-Semantik für „Wert vor N Zeiteinheiten",
    analog ``pandas.merge_asof``). Gibt es keinen Snapshot, der alt genug ist, ⇒ ``None`` für
    dieses Fenster — keine erfundene Differenz aus zu junger Historie."""
    if current_mid is None:
        return dict(_EMPTY_DELTA)
    result: dict[str, float | None] = {}
    for name, window in _WINDOWS.items():
        cutoff = now - window
        candidates = [
            entry for entry in history
            if entry.get("mid") is not None and _ensure_aware(entry["fetched_at"]) <= cutoff
        ]
        if not candidates:
            result[name] = None
            continue
        reference = max(candidates, key=lambda entry: _ensure_aware(entry["fetched_at"]))
        result[name] = current_mid - float(reference["mid"])
    return result


def evaluate_snapshot(
    *, bid: float | None, ask: float | None,
    depth_bid_usd: float | None, depth_ask_usd: float | None,
    volume_24h_usd: float | None, trade_count_24h: int | None,
    fetched_at: datetime, end_date: datetime | None = None,
    history: Sequence[Mapping] = (),
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
    now: datetime | None = None,
) -> EventState:
    """Bewertet EINEN Snapshot: leeres Buch, Tiefe, Spread, 24h-Volumen, Trade-Anzahl,
    Datenalterung, Restlaufzeit — schlimmster/erster zutreffender Grund gewinnt (Reihenfolge
    wie ``core/data_quality.py::evaluate_quality``). Bei ``usable=True`` wird zusätzlich Δp
    über 1h/6h/24h aus ``history`` berechnet.

    ``now`` ist injizierbar (Tests, reproduzierbare Restlaufzeit-/Alterungsprüfung); ohne
    Angabe gilt UTC-jetzt."""
    now = _ensure_aware(now) if now is not None else datetime.now(timezone.utc)
    fetched_at = _ensure_aware(fetched_at)

    if bid is None or ask is None:
        return _reject("book_empty", "Orderbuch leer (kein Bid und/oder Ask).")

    mid = compute_mid(bid, ask)

    if (depth_bid_usd is None or depth_ask_usd is None
            or min(depth_bid_usd, depth_ask_usd) < thresholds.min_depth_usd):
        return _reject(
            "depth_thin",
            f"Buchtiefe unter Minimum ({thresholds.min_depth_usd:.0f} $).",
            probability=mid)

    spread_bps = compute_spread_bps(bid, ask)
    if spread_bps is None or spread_bps > thresholds.max_spread_bps:
        shown = "n/a" if spread_bps is None else f"{spread_bps:.0f}"
        return _reject(
            "spread_wide",
            f"Spread {shown} bps über Limit {thresholds.max_spread_bps:.0f} bps.",
            probability=mid)

    if volume_24h_usd is None or volume_24h_usd < thresholds.min_volume_24h_usd:
        return _reject(
            "volume_low",
            f"24h-Volumen unter Minimum ({thresholds.min_volume_24h_usd:.0f} $).",
            probability=mid)

    if trade_count_24h is None or trade_count_24h < thresholds.min_trade_count_24h:
        return _reject(
            "trades_few",
            f"Weniger als {thresholds.min_trade_count_24h} Trades in 24h "
            f"({trade_count_24h if trade_count_24h is not None else 0}).",
            probability=mid)

    age_seconds = (now - fetched_at).total_seconds()
    if age_seconds > thresholds.max_snapshot_age_seconds:
        return _reject(
            "snapshot_stale",
            f"Snapshot ist {age_seconds:.0f}s alt (Limit "
            f"{thresholds.max_snapshot_age_seconds:.0f}s).",
            probability=mid)

    if end_date is not None:
        remaining = (_ensure_aware(end_date) - now).total_seconds()
        if remaining <= thresholds.min_time_to_resolution_seconds:
            return _reject(
                "near_resolution",
                f"Nur noch {remaining:.0f}s bis Auflösung (Limit "
                f"{thresholds.min_time_to_resolution_seconds:.0f}s).",
                probability=mid)

    delta = _compute_deltas(mid, history, now=now)
    return EventState(usable=True, probability=mid, delta=delta, reject_code="", reason="")
