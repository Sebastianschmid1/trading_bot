"""Tests für die Rohdatenarchiv-Verdrahtung im Shadow-Zyklus (OBS-RAWARCHIV).

Bis zu diesem Fix war `core/raw_data_archive.py` vollständig gebaut, aber ohne Aufrufer
außerhalb von Tests — `write_and_record`/`db.record_raw_data_archive_entry` liefen im
laufenden Betrieb nie. Diese Tests treiben den tatsächlich verdrahteten Weg
(`shadow_scheduler.generate_and_record` → `archive_signal_bars` → `write_and_record` →
`db.record_raw_data_archive_entry`) und prüfen danach `db.list_raw_data_archive_entries` —
nicht nur `write_and_record` direkt.
"""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from stockbot import config
from stockbot.core import db, raw_data_archive
from stockbot.market import analyzer, provider_factory
from stockbot.research import shadow_scheduler


class _FakeProvider:
    """Minimaler Fake für `MarketDataProvider.get_bars_batch` (nur was der Archiv-Pfad braucht)."""

    def __init__(self, bars_by_ticker: dict, *, provider_name: str = "fake_signal"):
        self._bars = bars_by_ticker
        self.provider_name = provider_name

    def get_bars_batch(self, tickers, *, interval, period, prepost=True):
        return {t: self._bars[t] for t in tickers if t in self._bars}


class _BoomProvider:
    """Fake, der beim Bar-Abruf immer scheitert — für den Fail-open-Test."""

    provider_name = "boom"

    def get_bars_batch(self, *args, **kwargs):
        raise RuntimeError("Marktdaten-Abruf kaputt")


def _bars(rows: int = 1) -> pd.DataFrame:
    idx = pd.date_range("2026-07-01", periods=rows, freq="D")
    return pd.DataFrame(
        {"Open": [100.0] * rows, "High": [101.0] * rows, "Low": [99.0] * rows,
         "Close": [100.5] * rows, "Volume": [1000] * rows}, index=idx)


@pytest.fixture(autouse=True)
def fresh_db():
    d = tempfile.mkdtemp(prefix="shadowschedtest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()
    yield


@pytest.fixture(autouse=True)
def _reset_provider_and_flag():
    provider_factory.reset_signal_provider()
    shadow_scheduler._archive_disabled_logged = False
    yield
    provider_factory.reset_signal_provider()
    shadow_scheduler._archive_disabled_logged = False


# ── Default AUS ───────────────────────────────────────────────────────────────

def test_archive_disabled_by_default():
    assert config.RAW_DATA_ARCHIVE_ENABLED is False


def test_archive_signal_bars_writes_nothing_when_disabled(tmp_path):
    provider_factory.set_signal_provider(_FakeProvider({"AAPL": _bars()}))
    stored = shadow_scheduler.archive_signal_bars(["AAPL"], base_dir=tmp_path)
    assert stored == 0
    assert db.list_raw_data_archive_entries() == []
    assert list(tmp_path.iterdir()) == []


def test_archive_disabled_logs_only_once(monkeypatch):
    # Bewusst NICHT über caplog geprüft: `bot.py` ruft beim Import `logging.basicConfig()` —
    # in der Vollsuite fängt caplog Meldungen des Modul-Loggers dadurch nicht zuverlässig ein
    # (propagation-/Handler-abhängig, siehe test_quote_context.py). Stattdessen wird der
    # Logger direkt beobachtet.
    monkeypatch.setattr(config, "RAW_DATA_ARCHIVE_ENABLED", False)
    calls = []
    monkeypatch.setattr(shadow_scheduler.log, "info", lambda msg, *a, **k: calls.append(msg))

    shadow_scheduler.archive_signal_bars(["AAPL"])
    shadow_scheduler.archive_signal_bars(["AAPL"])
    shadow_scheduler.archive_signal_bars(["AAPL"])

    assert len(calls) == 1
    assert "deaktiviert" in calls[0]


# ── Eingeschaltet: der verdrahtete Weg schreibt tatsächlich ──────────────────

def test_generate_and_record_archives_raw_bars_via_the_wired_job(monkeypatch, tmp_path):
    """Treibt den echten Job-Pfad (kein direkter write_and_record-Aufruf)."""
    monkeypatch.setattr(config, "RAW_DATA_ARCHIVE_ENABLED", True)
    monkeypatch.setattr(analyzer, "get_top_signals",
                        lambda *a, **k: [{"ticker": "AAPL", "strategy": "standard"},
                                         {"ticker": "MSFT", "strategy": "standard"}])
    provider_factory.set_signal_provider(
        _FakeProvider({"AAPL": _bars(), "MSFT": _bars()}, provider_name="alpaca_paper"))

    shadow_scheduler.generate_and_record(archive_base_dir=tmp_path)

    rows = db.list_raw_data_archive_entries()
    assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}
    assert all(r["provider"] == "alpaca_paper" for r in rows)
    assert all(Path(r["file_path"]).exists() for r in rows)


def test_archive_signal_bars_skips_tickers_without_bars(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DATA_ARCHIVE_ENABLED", True)
    provider_factory.set_signal_provider(_FakeProvider({"AAPL": _bars()}))

    stored = shadow_scheduler.archive_signal_bars(["AAPL", "XYZ", ""], base_dir=tmp_path)

    assert stored == 1
    assert db.list_raw_data_archive_entries("XYZ") == []
    assert len(db.list_raw_data_archive_entries("AAPL")) == 1


# ── Fail-open ─────────────────────────────────────────────────────────────────

def test_archive_signal_bars_fail_open_when_provider_raises(monkeypatch):
    # Direkte Logger-Beobachtung statt caplog — Begründung siehe
    # test_archive_disabled_logs_only_once oben / test_quote_context.py.
    monkeypatch.setattr(config, "RAW_DATA_ARCHIVE_ENABLED", True)
    provider_factory.set_signal_provider(_BoomProvider())
    calls = []
    monkeypatch.setattr(shadow_scheduler.log, "warning", lambda msg, *a, **k: calls.append(msg))

    stored = shadow_scheduler.archive_signal_bars(["AAPL"])

    assert stored == 0
    assert db.list_raw_data_archive_entries() == []
    assert calls and "Rohdatenarchiv" in calls[0]


def test_archive_signal_bars_fail_open_when_write_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RAW_DATA_ARCHIVE_ENABLED", True)
    provider_factory.set_signal_provider(_FakeProvider({"AAPL": _bars()}))

    def boom(*args, **kwargs):
        raise OSError("Platte voll")

    monkeypatch.setattr(raw_data_archive, "write_and_record", boom)

    stored = shadow_scheduler.archive_signal_bars(["AAPL"], base_dir=tmp_path)

    assert stored == 0
    assert db.list_raw_data_archive_entries() == []


def test_generate_and_record_shadow_cycle_unaffected_by_archive_failure(monkeypatch):
    """Ein kaputter Archiv-Schreibvorgang darf den Shadow-Zyklus nicht mitreißen (AC5)."""
    db.ensure_strategy_versions_published(code_commit="c1")
    monkeypatch.setattr(config, "RAW_DATA_ARCHIVE_ENABLED", True)
    monkeypatch.setattr(analyzer, "get_top_signals",
                        lambda *a, **k: [{"ticker": "AAPL", "strategy": "standard"}])
    provider_factory.set_signal_provider(_BoomProvider())

    stored = shadow_scheduler.generate_and_record()

    assert stored == 1                                    # Shadow-Snapshot trotzdem persistiert
    assert db.list_raw_data_archive_entries() == []        # Archiv blieb leer, aber nichts crashte
