"""Rohdatenarchiv — Minimalversion (Phase 2, letzter DATA-Punkt, siehe docs/Plan.md §10.5,
docs/PLAN_CHECKLIST.md Phase 2).

Speichert unveränderte OHLCV-Rohdaten (DATA-003 `MarketDataProvider.get_bars`) als Parquet,
partitioniert nach Symbol/Datum/Timeframe (Plan.md §10.5) — getrennt von abgeleiteten Features
(Indikatoren etc. bleiben, wo sie heute berechnet werden; dieses Modul schreibt NUR die
Rohdaten).

Metadaten [Provider, Abrufzeit, Zeilenanzahl, Dateipfad] werden über den DB-Seam persistiert
(`db.record_raw_data_archive_entry`) — nach dem Postgres-Cutover (W3.1) ist der frühere
Staging-Blocker aufgelöst. `write_bars` liefert weiterhin das reine `RawDataArchiveEntry`-
Wertobjekt (IO-frei bzgl. DB); `write_and_record` schreibt die Parquet-Datei UND persistiert die
Metadaten in einem Schritt (W3.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from stockbot.paths import DATA_DIR

RAW_DATA_ARCHIVE_DIR = DATA_DIR / "raw_archive"


@dataclass(frozen=True)
class RawDataArchiveEntry:
    """Metadaten-Zeile für eine archivierte Rohdaten-Datei — dieselben Provenance-Felder wie
    `market_data.Quote`/`domain.Signal` (DATA-003/DATA-005), plus archivspezifische Felder."""
    symbol: str
    trading_date: date
    timeframe: str
    provider: str
    fetched_at: datetime
    row_count: int
    file_path: str


def parquet_path(
    symbol: str, trading_date: date, timeframe: str, *, base_dir: Path = RAW_DATA_ARCHIVE_DIR
) -> Path:
    """Partitionierter Pfad: `<base_dir>/<SYMBOL>/<YYYY-MM-DD>/<timeframe>.parquet`
    (Plan.md §10.5 „Partitionierung nach Symbol, Datum und Timeframe")."""
    return base_dir / symbol.upper() / trading_date.isoformat() / f"{timeframe}.parquet"


def write_bars(
    symbol: str, trading_date: date, timeframe: str, bars: pd.DataFrame, *,
    provider: str, fetched_at: datetime, base_dir: Path = RAW_DATA_ARCHIVE_DIR,
) -> RawDataArchiveEntry:
    """Schreibt unveränderte OHLCV-Rohdaten als Parquet.

    Überschreibt eine bestehende Datei derselben Partition — ein erneuter Abruf desselben
    Handelstags/Timeframes ersetzt den alten Snapshot, statt zu duplizieren/anzuhängen."""
    path = parquet_path(symbol, trading_date, timeframe, base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    bars.to_parquet(path)
    return RawDataArchiveEntry(
        symbol=symbol.upper(), trading_date=trading_date, timeframe=timeframe,
        provider=provider, fetched_at=fetched_at, row_count=len(bars), file_path=str(path))


def write_and_record(
    symbol: str, trading_date: date, timeframe: str, bars: pd.DataFrame, *,
    provider: str, fetched_at: datetime, base_dir: Path = RAW_DATA_ARCHIVE_DIR, record=None,
) -> RawDataArchiveEntry:
    """Schreibt die Rohdaten (Parquet) UND persistiert die Metadaten über den DB-Seam (W3.5).

    ``record`` ist injizierbar (Tests brauchen keine DB); Default ist
    ``db.record_raw_data_archive_entry`` (lazy importiert, kein Modul-Zyklus)."""
    entry = write_bars(symbol, trading_date, timeframe, bars,
                       provider=provider, fetched_at=fetched_at, base_dir=base_dir)
    if record is None:
        from stockbot.core import db
        record = db.record_raw_data_archive_entry
    record(entry)
    return entry


def read_bars(
    symbol: str, trading_date: date, timeframe: str, *, base_dir: Path = RAW_DATA_ARCHIVE_DIR
) -> pd.DataFrame | None:
    """Liest zuvor archivierte Rohdaten zurück, `None` wenn für diese Partition nichts archiviert ist."""
    path = parquet_path(symbol, trading_date, timeframe, base_dir=base_dir)
    if not path.exists():
        return None
    return pd.read_parquet(path)
