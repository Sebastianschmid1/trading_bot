"""Planbare Shadow-Signalerzeugung (W3.5, RES-002) + Rohdatenarchivierung (OBS-RAWARCHIV).

Erzeugt regelmäßig Schatten-Signale aus der produktiven Analyse und persistiert je Signal eine
Shadow-``PerformanceSnapshot`` über den DB-Seam (`db.record_shadow_snapshot`). Der so gefüllte
Shadow-Report wird im Dashboard strikt getrennt vom Paper-Report angezeigt (Mode-Isolation).

Bewusst **entry-only**: eine gerade erzeugte Shadow-Beobachtung hat noch keine realisierte P&L
(`net_pnl=None`) — es wird keine P&L erfunden. Echte Shadow-P&L (Exit-Simulation über
`research/shadow.py::simulate_exit`) ist ein separater, größerer Schritt.

Derselbe Zyklus archiviert nebenbei die Tagesbars der gerade betrachteten Ticker im
Rohdatenarchiv (`core/raw_data_archive.py`) — das Modul existierte seit W3.5, hatte aber nie
einen Aufrufer (`write_and_record`/`db.record_raw_data_archive_entry` liefen nie). Statt eines
eigenen Abrufs im (synchronen) Signal-/Handelspfad hängt sich das an den ohnehin laufenden,
vom Signalpfad bereits entkoppelten Hintergrundjob (`config.RAW_DATA_ARCHIVE_ENABLED` für
Default und Kostenschätzung).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from stockbot import config
from stockbot.core import db, raw_data_archive
from stockbot.core.domain import Mode, PerformanceSnapshot
from stockbot.core.raw_data_archive import RAW_DATA_ARCHIVE_DIR
from stockbot.market import provider_factory

log = logging.getLogger(__name__)

_archive_disabled_logged = False


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


def archive_signal_bars(tickers: Iterable[str], *, base_dir: Path = RAW_DATA_ARCHIVE_DIR) -> int:
    """Archiviert die Tagesbars der übergebenen Ticker im Rohdatenarchiv (OBS-RAWARCHIV).

    Nebeneffekt des Shadow-Zyklus — die Ticker (aktuelle Top-Signale) sind ohnehin schon bekannt,
    ein zusätzlicher Analyse-Lauf entfällt. Default AUS (`config.RAW_DATA_ARCHIVE_ENABLED`, siehe
    dort für die Kostenschätzung); ist das Flag aus, wird nichts geschrieben und höchstens
    einmalig geloggt. Fail-open: ein Abruf- oder Schreibfehler bricht den Shadow-Zyklus nicht,
    wird aber mit Kontext geloggt. `base_dir` ist injizierbar (Tests brauchen kein echtes
    `data/`-Verzeichnis). Gibt die Anzahl archivierter Ticker zurück."""
    global _archive_disabled_logged
    if not config.RAW_DATA_ARCHIVE_ENABLED:
        if not _archive_disabled_logged:
            log.info("Rohdatenarchiv deaktiviert (RAW_DATA_ARCHIVE_ENABLED=false) — "
                     "es wird nichts archiviert.")
            _archive_disabled_logged = True
        return 0
    symbols = sorted({t for t in tickers if t})
    if not symbols:
        return 0
    try:
        provider = provider_factory.get_signal_provider()
        bars_by_ticker = provider.get_bars_batch(symbols, interval="1d", period="1d")
        fetched_at = datetime.now(timezone.utc)
        stored = 0
        for ticker, bars in bars_by_ticker.items():
            if bars is None or not len(bars):
                continue
            # Je Ticker fangen: ein einzelner Schreibfehler darf nicht die Archivierung der
            # uebrigen Symbole verschlucken — sonst entstehen stille Luecken im Archiv.
            try:
                trading_date = bars.index[-1].date()
                raw_data_archive.write_and_record(
                    ticker, trading_date, "1d", bars, provider=provider.provider_name,
                    fetched_at=fetched_at, base_dir=base_dir)
                stored += 1
            except Exception as e:
                log.warning(f"Rohdatenarchiv: {ticker} nicht archiviert ({e})")
        return stored
    except Exception as e:
        log.warning(f"Rohdatenarchiv: Archivierung fehlgeschlagen ({e})")
        return 0


def generate_and_record(
    tickers: list[str] | None = None, *, archive_base_dir: Path = RAW_DATA_ARCHIVE_DIR,
) -> int:
    """Voller Zyklus: produktive Analyse (Alpaca-Signalprovider) → Shadow-Snapshots +
    Rohdatenarchiv (`archive_signal_bars`, OBS-RAWARCHIV). Bricht nie — Fehler in der Analyse
    werden geloggt und der Zyklus liefert 0. `archive_base_dir` nur für Tests relevant."""
    from stockbot.market import analyzer
    try:
        signals = (analyzer.analyze_universe(tickers) if tickers is not None
                   else analyzer.get_top_signals())
    except Exception as e:
        log.warning(f"Shadow-Zyklus: Analyse fehlgeschlagen ({e})")
        return 0
    archive_signal_bars((s.get("ticker", "") for s in signals), base_dir=archive_base_dir)
    return run_shadow_cycle(signals)
