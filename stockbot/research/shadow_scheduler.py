"""Planbare Shadow-Signalerzeugung (W3.5, RES-002).

Erzeugt regelmäßig Schatten-Signale aus der produktiven Analyse und persistiert je Signal eine
Shadow-``PerformanceSnapshot`` über den DB-Seam (`db.record_shadow_snapshot`). Der so gefüllte
Shadow-Report wird im Dashboard strikt getrennt vom Paper-Report angezeigt (Mode-Isolation).

Bewusst **entry-only**: eine gerade erzeugte Shadow-Beobachtung hat noch keine realisierte P&L
(`net_pnl=None`) — es wird keine P&L erfunden. Echte Shadow-P&L (Exit-Simulation über
`research/shadow.py::simulate_exit`) ist ein separater, größerer Schritt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from stockbot.core import db
from stockbot.core.domain import Mode, PerformanceSnapshot

log = logging.getLogger(__name__)


def run_shadow_cycle(signals: Iterable[dict]) -> int:
    """Persistiert je Produktions-Signal-Dict (mind. ``ticker``/``strategy``) eine Shadow-Snapshot.

    Signale ohne auflösbare (produktive) ``strategy_version_id`` werden übersprungen. Gibt die
    Anzahl gespeicherter Shadow-Snapshots zurück."""
    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    for signal in signals:
        version_id = signal.get("strategy_version_id") or db.resolve_strategy_version_id(
            signal.get("strategy") or "standard")
        if not version_id:
            continue
        snapshot = PerformanceSnapshot(
            id=None, strategy_version_id=int(version_id), mode=Mode.SHADOW,
            captured_at=now, net_pnl=None, open_risk=None)
        db.record_shadow_snapshot(snapshot, ticker=signal.get("ticker", ""))
        stored += 1
    return stored


def generate_and_record(tickers: list[str] | None = None) -> int:
    """Voller Zyklus: produktive Analyse (Alpaca-Signalprovider) → Shadow-Snapshots. Bricht nie —
    Fehler in der Analyse werden geloggt und der Zyklus liefert 0."""
    from stockbot.market import analyzer
    try:
        signals = (analyzer.analyze_universe(tickers) if tickers is not None
                   else analyzer.get_top_signals())
    except Exception as e:
        log.warning(f"Shadow-Zyklus: Analyse fehlgeschlagen ({e})")
        return 0
    return run_shadow_cycle(signals)
