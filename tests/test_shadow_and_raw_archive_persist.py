"""Persistenz: Rohdatenarchiv-Metadaten + Shadow-Snapshots + Shadow-Zyklus (W3.5)."""

import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from stockbot.core import db, raw_data_archive
from stockbot.core.domain import Mode, PerformanceSnapshot
from stockbot.research import shadow_scheduler


@pytest.fixture(autouse=True)
def fresh_db():
    d = tempfile.mkdtemp(prefix="w35test_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()
    yield


def _entry(symbol="AAPL", tf="1d", rows=3):
    return raw_data_archive.RawDataArchiveEntry(
        symbol=symbol, trading_date=date(2026, 7, 1), timeframe=tf,
        provider="alpaca_paper", fetched_at=datetime(2026, 7, 1, 20, tzinfo=timezone.utc),
        row_count=rows, file_path=f"/x/{symbol}/{tf}.parquet")


def test_record_raw_data_archive_entry_persists_and_lists():
    db.record_raw_data_archive_entry(_entry())
    rows = db.list_raw_data_archive_entries()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL" and rows[0]["provider"] == "alpaca_paper"
    assert rows[0]["row_count"] == 3


def test_record_raw_data_archive_is_idempotent_per_partition():
    first = db.record_raw_data_archive_entry(_entry(rows=3))
    second = db.record_raw_data_archive_entry(_entry(rows=9))   # gleiche Partition, neue Zeilenzahl
    assert first == second
    rows = db.list_raw_data_archive_entries("AAPL")
    assert len(rows) == 1 and rows[0]["row_count"] == 9   # aktualisiert, nicht dupliziert


def test_write_and_record_writes_file_and_persists_metadata(tmp_path):
    bars = pd.DataFrame({"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5],
                         "Volume": [100]}, index=pd.DatetimeIndex(["2026-07-01"]))
    entry = raw_data_archive.write_and_record(
        "msft", date(2026, 7, 1), "1d", bars, provider="alpaca_paper",
        fetched_at=datetime(2026, 7, 1, 20, tzinfo=timezone.utc), base_dir=tmp_path)
    assert Path(entry.file_path).exists()
    assert db.list_raw_data_archive_entries("MSFT")[0]["row_count"] == 1


def test_record_shadow_snapshot_rejects_non_shadow_mode():
    snap = PerformanceSnapshot(id=None, strategy_version_id=1, mode=Mode.PAPER,
                               captured_at="2026-07-01", net_pnl=1.0)
    with pytest.raises(ValueError):
        db.record_shadow_snapshot(snap)


def test_shadow_snapshots_roundtrip_for_report():
    snap = PerformanceSnapshot(id=None, strategy_version_id=5, mode=Mode.SHADOW,
                               captured_at="2026-07-01T10:00:00", net_pnl=None, open_risk=None)
    db.record_shadow_snapshot(snap, ticker="AAPL")
    loaded = db.get_shadow_snapshots()
    assert len(loaded) == 1
    assert loaded[0].mode is Mode.SHADOW and loaded[0].strategy_version_id == 5


def test_run_shadow_cycle_persists_snapshots_for_resolvable_strategies():
    db.ensure_strategy_versions_published(code_commit="c1")
    signals = [{"ticker": "AAPL", "strategy": "standard"},
               {"ticker": "XYZ", "strategy": "adx_trend"}]   # research → keine Version → übersprungen
    stored = shadow_scheduler.run_shadow_cycle(signals)
    assert stored == 1
    snaps = db.get_shadow_snapshots()
    assert len(snaps) == 1
    assert snaps[0].strategy_version_id == db.resolve_strategy_version_id("standard")
