"""Datenqualitäts-Gates (Phase 2 / DATA-004, siehe docs/Plan.md §10.4,
docs/PLAN_CHECKLIST.md Phase 2).

Reine, IO-freie Prüf-Funktionen auf den DATA-003-Wertobjekten (`Quote`/`MarketStatus`/
`CorporateAction`) und `pandas`-Bars — bewusst nach demselben Muster wie
`stockbot/core/risk.py::pretrade_check` (ein Entscheidungs-Dataclass statt Exceptions, damit
Aufrufer den Ablehnungsgrund direkt anzeigen können, z. B. im UI).

Im Produktions-Pre-Trade-Gate verdrahtet (Gate P2: „veraltete Quotes blockieren Orders"):
`stockbot/core/risk.py::pretrade_check` ruft `check_quote_age`/`check_spread` auf und lehnt
Orders auf veralteten Quotes bzw. zu weiten Spreads ab. Die dafür nötigen Quote-Eingaben lädt
der Kontext-Loader `stockbot/execution/risk_context.py` (fail-open) über den `MarketDataProvider`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from stockbot.core.market_data import CorporateAction, MarketStatus, Quote


@dataclass(frozen=True)
class QualityDecision:
    """Ergebnis eines Qualitäts-Checks. `ok=False` ⇒ die Berechnung/das Signal darf sich NICHT
    auf diese Daten stützen."""
    ok: bool
    reason: str = ""
    code: str = ""          # maschinenlesbarer Grund (z. B. "quote_stale")


_OK = QualityDecision(ok=True)

_DEFAULT_BAR_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def check_quote_age(
    quote: Quote, *, max_age_seconds: float, now: datetime | None = None
) -> QualityDecision:
    """Plan.md §10.4 „Quote nicht älter als Grenzwert"."""
    now = now or datetime.now(timezone.utc)
    age = (now - quote.as_of).total_seconds()
    if age > max_age_seconds:
        return QualityDecision(
            False, f"Quote ist {age:.0f}s alt (Limit {max_age_seconds:.0f}s).", "quote_stale")
    return _OK


def check_spread(quote: Quote, *, max_spread_bps: float) -> QualityDecision:
    """Plan.md §10.4 „Spread nicht größer als Grenzwert". Ohne Bid/Ask (z. B. verzögerter
    Research-Feed ohne Orderbuch) wird NICHT geprüft — der Check ist dann nicht anwendbar,
    kein Ablehnungsgrund."""
    if quote.bid is None or quote.ask is None or quote.bid <= 0:
        return _OK
    spread_bps = (quote.ask - quote.bid) / quote.bid * 10_000
    if spread_bps > max_spread_bps:
        return QualityDecision(
            False, f"Spread {spread_bps:.0f} bps über Limit {max_spread_bps:.0f} bps.",
            "spread_wide")
    return _OK


def check_bars_complete(
    bars: pd.DataFrame, *, required_columns: tuple[str, ...] = _DEFAULT_BAR_COLUMNS,
    min_rows: int = 1,
) -> QualityDecision:
    """Plan.md §10.4 „Bar vollständig"."""
    if bars is None or bars.empty:
        return QualityDecision(False, "Keine Bars vorhanden.", "bars_missing")
    missing = [c for c in required_columns if c not in bars.columns]
    if missing:
        return QualityDecision(False, f"Bars fehlen Spalten: {missing}.", "bars_incomplete")
    if len(bars) < min_rows:
        return QualityDecision(
            False, f"Nur {len(bars)} Bar(s) vorhanden, erwartet mind. {min_rows}.",
            "bars_incomplete")
    return _OK


def check_no_nan(
    bars: pd.DataFrame, *, columns: tuple[str, ...] = _DEFAULT_BAR_COLUMNS
) -> QualityDecision:
    """Plan.md §10.4 „keine NaN-Kernwerte"."""
    present = [c for c in columns if c in bars.columns]
    if present and bars[present].isna().any().any():
        return QualityDecision(False, "Kernwerte (OHLCV) enthalten NaN.", "nan_values")
    return _OK


def check_symbol_active(*, tradable: bool) -> QualityDecision:
    """Plan.md §10.4 „Symbol aktiv"."""
    if not tradable:
        return QualityDecision(False, "Symbol ist nicht handelbar (inaktiv/delisted).",
                               "symbol_inactive")
    return _OK


def check_no_halt(*, halted: bool) -> QualityDecision:
    """Plan.md §10.4 „kein Halt"."""
    if halted:
        return QualityDecision(False, "Symbol ist aktuell im Handelsstopp (Halt).",
                               "trading_halted")
    return _OK


def check_market_status(status: MarketStatus, *, exchange_open: bool) -> QualityDecision:
    """Plan.md §10.4 „Marktstatus korrekt": vergleicht den Provider-Marktstatus (DATA-003) mit
    dem Exchange-Kalender (DATA-001/DATA-002) — eine Diskrepanz kann auf einen ungeplanten Halt
    oder einen Provider-Fehler hindeuten."""
    if status.is_open != exchange_open:
        return QualityDecision(
            False,
            f"Marktstatus-Diskrepanz: Provider meldet is_open={status.is_open}, "
            f"Exchange-Kalender erwartet {exchange_open}.",
            "market_status_mismatch")
    return _OK


def check_corporate_actions(
    actions: list[CorporateAction], *, since: datetime,
    blocking_types: tuple[str, ...] = ("split", "forward_split", "forward_splits",
                                       "reverse_split", "reverse_splits"),
) -> QualityDecision:
    """Plan.md §10.4 „Corporate Actions berücksichtigt": unverarbeitete Splits seit `since`
    können Bars/Kennzahlen falsch skalieren — konservativ blockieren statt still falsch rechnen."""
    recent = [a for a in actions if a.ex_date >= since and a.action_type in blocking_types]
    if recent:
        return QualityDecision(
            False,
            f"{len(recent)} unverarbeitete Kursanpassung(en) seit {since.date()} "
            f"({', '.join(sorted({a.action_type for a in recent}))}).",
            "corporate_action_pending")
    return _OK


def evaluate_quality(
    *, quote: Quote | None = None, bars: pd.DataFrame | None = None,
    tradable: bool = True, halted: bool = False,
    market_status: MarketStatus | None = None, exchange_open: bool | None = None,
    corporate_actions: list[CorporateAction] | None = None,
    corporate_actions_since: datetime | None = None,
    max_quote_age_seconds: float | None = None, max_spread_bps: float | None = None,
) -> QualityDecision:
    """Bündelt alle Einzel-Checks in fester Reihenfolge (schlimmster Grund zuerst) — Seam fürs
    Wiring in den Produktionssignalpfad, analog zu `risk.py::pretrade_check`.

    Jeder Check läuft nur, wenn die dafür nötigen Eingaben übergeben wurden (z. B. `bars=None`,
    wenn nur ein Quote geprüft wird) — kein Check blockiert allein wegen fehlender Eingabe."""
    if not tradable:
        return check_symbol_active(tradable=tradable)
    if halted:
        return check_no_halt(halted=halted)
    if market_status is not None and exchange_open is not None:
        decision = check_market_status(market_status, exchange_open=exchange_open)
        if not decision.ok:
            return decision
    if quote is not None and max_quote_age_seconds is not None:
        decision = check_quote_age(quote, max_age_seconds=max_quote_age_seconds)
        if not decision.ok:
            return decision
    if quote is not None and max_spread_bps is not None:
        decision = check_spread(quote, max_spread_bps=max_spread_bps)
        if not decision.ok:
            return decision
    if bars is not None:
        decision = check_bars_complete(bars)
        if not decision.ok:
            return decision
        decision = check_no_nan(bars)
        if not decision.ok:
            return decision
    if corporate_actions is not None and corporate_actions_since is not None:
        decision = check_corporate_actions(corporate_actions, since=corporate_actions_since)
        if not decision.ok:
            return decision
    return _OK
