"""Persistenz-Tests für die Polymarket-Datenschicht (PM-0): SCHEMA_SQL-Tabellen,
DB-Seam-Funktionen in `core/db.py` und der Zeitvertrag (naiver UTC-String beim Schreiben,
aware->naiv beim Lesen — siehe AGENTS.md „DB-Konventionen"/`core/burn_in.py::_as_utc_text`).
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.core import db
from stockbot.research.polymarket import MarketInfo, MarketSnapshot


@pytest.fixture(autouse=True)
def fresh_db():
    d = tempfile.mkdtemp(prefix="pmtest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()
    yield


def _info(condition_id="0xabc123", **overrides):
    kwargs = dict(
        condition_id=condition_id, token_id="111111", slug="will-x-happen",
        question="Will X happen?", resolution_source="https://example.org/rules",
        resolution_rules="Resolves YES if X happens.", end_date="2026-12-31T00:00:00Z",
        category="Politics",
    )
    kwargs.update(overrides)
    return MarketInfo(**kwargs)


def _snapshot(condition_id="0xabc123", fetched_at=None, **overrides):
    kwargs = dict(
        condition_id=condition_id,
        fetched_at=fetched_at or datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc),
        bid=0.40, ask=0.42, depth_bid_usd=1000.0, depth_ask_usd=900.0,
        volume_24h_usd=5000.0, liquidity_usd=20000.0, trade_count_24h=15,
        last_trade_at=datetime(2026, 8, 13, 11, 55, 0, tzinfo=timezone.utc),
        raw={"market": {"question": "Will X happen?"}, "book": {}, "trades": []},
    )
    kwargs.update(overrides)
    return MarketSnapshot(**kwargs)


# ── polymarket_markets: Upsert ────────────────────────────────────────────────

def test_upsert_polymarket_market_inserts_new_row():
    row_id = db.upsert_polymarket_market(_info())
    markets = db.list_polymarket_markets()
    assert len(markets) == 1
    assert markets[0]["id"] == row_id
    assert markets[0]["condition_id"] == "0xabc123"
    assert markets[0]["question"] == "Will X happen?"
    assert markets[0]["first_seen_at"] == markets[0]["last_seen_at"]


def test_upsert_polymarket_market_updates_existing_row_idempotently():
    first_id = db.upsert_polymarket_market(_info(question="Will X happen?"))
    second_id = db.upsert_polymarket_market(_info(question="Will X happen (updated wording)?"))
    assert first_id == second_id
    markets = db.list_polymarket_markets()
    assert len(markets) == 1   # kein Duplikat je condition_id
    assert markets[0]["question"] == "Will X happen (updated wording)?"


# ── polymarket_snapshots: Persistenz + Historie ──────────────────────────────

def test_record_polymarket_snapshot_persists_and_lists():
    db.upsert_polymarket_market(_info())
    snap = _snapshot()
    db.record_polymarket_snapshot(
        snap, mid=0.41, spread_bps=500.0, usable=True, reject_code="",
        raw_file_path="/data/polymarket_archive/0xabc123/2026-08-13/120000.json")

    rows = db.list_polymarket_snapshots("0xabc123")
    assert len(rows) == 1
    row = rows[0]
    assert row["mid"] == 0.41
    assert row["spread_bps"] == 500.0
    assert bool(row["usable"]) is True
    assert row["reject_code"] == ""
    assert row["raw_file_path"].endswith("120000.json")
    assert row["bid"] == 0.40 and row["ask"] == 0.42


def test_record_polymarket_snapshot_rejected_row_persists_reject_code():
    db.upsert_polymarket_market(_info())
    snap = _snapshot(bid=None, ask=None)
    db.record_polymarket_snapshot(
        snap, mid=None, spread_bps=None, usable=False, reject_code="book_empty")
    row = db.list_polymarket_snapshots("0xabc123")[0]
    assert bool(row["usable"]) is False
    assert row["reject_code"] == "book_empty"
    assert row["mid"] is None


def test_polymarket_snapshot_history_returns_ascending_and_filters_by_since():
    db.upsert_polymarket_market(_info())
    base = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
    for offset_hours, mid in ((-30, 0.20), (-10, 0.30), (0, 0.40)):
        snap = _snapshot(fetched_at=base + timedelta(hours=offset_hours))
        db.record_polymarket_snapshot(snap, mid=mid, spread_bps=100.0, usable=True,
                                      reject_code="")

    history = db.polymarket_snapshot_history("0xabc123", since=base - timedelta(hours=24))
    # Der Eintrag von vor 30h liegt außerhalb des 24h-Fensters -> nur 2 Zeilen
    assert len(history) == 2
    assert history[0]["fetched_at"] < history[1]["fetched_at"]   # aufsteigend sortiert
    assert history[0]["mid"] == 0.30
    assert history[1]["mid"] == 0.40


# ── Zeitvertrag (Acceptance Criterion 5) ─────────────────────────────────────

def test_snapshot_write_binds_naive_utc_timestamp_string():
    """`record_polymarket_snapshot` darf den Zeitstempel nie roh als aware datetime binden
    -- Postgres würde ihn dann als `timestamptz` interpretieren und der TEXT-Vergleich in
    `polymarket_snapshot_history` bräche hart (siehe AGENTS.md „Zeitvertrag")."""
    db.upsert_polymarket_market(_info())
    aware = datetime(2026, 8, 13, 9, 30, 0, tzinfo=timezone.utc)
    snap = _snapshot(fetched_at=aware, last_trade_at=aware)
    db.record_polymarket_snapshot(snap, mid=0.5, spread_bps=10.0, usable=True, reject_code="")

    row = db.list_polymarket_snapshots("0xabc123")[0]
    # Naiver 'YYYY-MM-DD HH:MM:SS'-String, KEIN 'T', kein '+00', kein 'Z'.
    assert row["fetched_at"] == "2026-08-13 09:30:00"
    assert row["last_trade_at"] == "2026-08-13 09:30:00"
    assert "+" not in row["fetched_at"] and "T" not in row["fetched_at"]


def test_snapshot_history_coerces_read_back_to_aware_utc():
    """Der Lesepfad liefert immer tz-aware UTC-`datetime`-Objekte zurück, unabhängig davon,
    dass die Spalte selbst naiv gespeichert ist -- Vorgabe: aware<->naiv-Koerzion beim
    Lesen (AGENTS.md „Zeitvertrag")."""
    db.upsert_polymarket_market(_info())
    aware = datetime(2026, 8, 13, 9, 30, 0, tzinfo=timezone.utc)
    db.record_polymarket_snapshot(_snapshot(fetched_at=aware), mid=0.5, spread_bps=10.0,
                                  usable=True, reject_code="")

    history = db.polymarket_snapshot_history("0xabc123", since=aware - timedelta(hours=1))
    assert len(history) == 1
    fetched_at = history[0]["fetched_at"]
    assert fetched_at.tzinfo is not None
    assert fetched_at.astimezone(timezone.utc) == aware


def test_polymarket_timestamp_helper_normalizes_naive_and_aware_and_strings():
    naive = datetime(2026, 8, 13, 9, 30, 0)
    aware = datetime(2026, 8, 13, 9, 30, 0, tzinfo=timezone.utc)
    assert db._polymarket_timestamp(naive) == "2026-08-13 09:30:00"
    assert db._polymarket_timestamp(aware) == "2026-08-13 09:30:00"
    assert db._polymarket_timestamp("2026-08-13T09:30:00Z") == "2026-08-13 09:30:00"
