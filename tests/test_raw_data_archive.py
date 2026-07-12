"""
Tests für das Rohdatenarchiv (letzter DATA-Punkt Phase 2, stockbot/core/raw_data_archive.py,
Plan.md §10.5).

Nutzt ein temporäres Verzeichnis als base_dir — kein Zugriff auf das echte data/-Verzeichnis.
"""

import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from stockbot.core import raw_data_archive as archive


def _tmp_dir():
    return Path(tempfile.mkdtemp(prefix="rawarchive_"))


def _bars():
    return pd.DataFrame({
        "Open": [100.0, 101.0], "High": [101.0, 102.0], "Low": [99.0, 100.5],
        "Close": [100.5, 101.5], "Volume": [1000, 1200],
    })


def test_parquet_path_partitions_by_symbol_date_timeframe():
    base = _tmp_dir()
    path = archive.parquet_path("aapl", date(2026, 6, 10), "1d", base_dir=base)
    assert path == base / "AAPL" / "2026-06-10" / "1d.parquet"


def test_write_bars_creates_file_and_returns_entry():
    base = _tmp_dir()
    fetched_at = datetime(2026, 6, 10, 16, 15, tzinfo=timezone.utc)
    entry = archive.write_bars(
        "AAPL", date(2026, 6, 10), "1d", _bars(),
        provider="yfinance_research", fetched_at=fetched_at, base_dir=base)

    assert entry.symbol == "AAPL"
    assert entry.trading_date == date(2026, 6, 10)
    assert entry.timeframe == "1d"
    assert entry.provider == "yfinance_research"
    assert entry.fetched_at == fetched_at
    assert entry.row_count == 2
    assert Path(entry.file_path).exists()


def test_write_bars_uppercases_symbol_in_partition():
    base = _tmp_dir()
    archive.write_bars("aapl", date(2026, 6, 10), "1d", _bars(),
                       provider="stub", fetched_at=datetime.now(timezone.utc), base_dir=base)
    assert (base / "AAPL" / "2026-06-10" / "1d.parquet").exists()


def test_read_bars_returns_none_when_nothing_archived():
    base = _tmp_dir()
    assert archive.read_bars("AAPL", date(2026, 6, 10), "1d", base_dir=base) is None


def test_write_then_read_bars_roundtrip():
    base = _tmp_dir()
    bars = _bars()
    archive.write_bars("AAPL", date(2026, 6, 10), "1d", bars,
                       provider="stub", fetched_at=datetime.now(timezone.utc), base_dir=base)
    back = archive.read_bars("AAPL", date(2026, 6, 10), "1d", base_dir=base)
    pd.testing.assert_frame_equal(back, bars)


def test_write_bars_overwrites_same_partition_instead_of_duplicating():
    base = _tmp_dir()
    archive.write_bars("AAPL", date(2026, 6, 10), "1d", _bars(),
                       provider="stub", fetched_at=datetime.now(timezone.utc), base_dir=base)
    smaller = _bars().iloc[:1]
    archive.write_bars("AAPL", date(2026, 6, 10), "1d", smaller,
                       provider="stub", fetched_at=datetime.now(timezone.utc), base_dir=base)
    back = archive.read_bars("AAPL", date(2026, 6, 10), "1d", base_dir=base)
    assert len(back) == 1   # zweiter Schreibvorgang hat ersetzt, nicht angehängt


def test_different_symbols_dates_timeframes_do_not_collide():
    base = _tmp_dir()
    archive.write_bars("AAPL", date(2026, 6, 10), "1d", _bars(),
                       provider="stub", fetched_at=datetime.now(timezone.utc), base_dir=base)
    archive.write_bars("MSFT", date(2026, 6, 10), "1d", _bars().iloc[:1],
                       provider="stub", fetched_at=datetime.now(timezone.utc), base_dir=base)
    archive.write_bars("AAPL", date(2026, 6, 11), "1d", _bars().iloc[:1],
                       provider="stub", fetched_at=datetime.now(timezone.utc), base_dir=base)
    archive.write_bars("AAPL", date(2026, 6, 10), "5m", _bars().iloc[:1],
                       provider="stub", fetched_at=datetime.now(timezone.utc), base_dir=base)

    assert len(archive.read_bars("AAPL", date(2026, 6, 10), "1d", base_dir=base)) == 2
    assert len(archive.read_bars("MSFT", date(2026, 6, 10), "1d", base_dir=base)) == 1
    assert len(archive.read_bars("AAPL", date(2026, 6, 11), "1d", base_dir=base)) == 1
    assert len(archive.read_bars("AAPL", date(2026, 6, 10), "5m", base_dir=base)) == 1
