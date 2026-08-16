"""Tests für den Polymarket-Erhebungszyklus (PM-0). Netzwerk und Rohdatenarchiv werden
gemockt/auf tmp_path umgeleitet — kein echter HTTP-Zugriff, keine Schreibzugriffe auf das
echte `data/`-Verzeichnis.

Der Zyklus ist bewusst NICHT registriert (kein `run_repeating` in `tgbot/bot.py`) — das prüft
dieses Modul nicht positiv (nichts zu testen), sondern der Grep-Nachweis im Handoff-Report.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stockbot.core import db
from stockbot.research import polymarket, polymarket_quality, polymarket_scheduler


@pytest.fixture(autouse=True)
def fresh_db():
    d = tempfile.mkdtemp(prefix="pmschedtest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()
    yield


def _market(condition_id="0xabc123", end_date="2026-12-31T00:00:00Z"):
    return {
        "conditionId": condition_id,
        "clobTokenIds": '["111111"]',
        "slug": "will-x-happen",
        "question": "Will X happen?",
        "resolutionSource": "https://example.org/rules",
        "description": "Resolves YES if X happens.",
        "endDate": end_date,
        "category": "Politics",
        "liquidity": "20000",
    }


def _good_snapshot(condition_id="0xabc123"):
    return polymarket.MarketSnapshot(
        condition_id=condition_id, fetched_at=datetime.now(timezone.utc),
        bid=0.40, ask=0.41, depth_bid_usd=1000.0, depth_ask_usd=1000.0,
        volume_24h_usd=5000.0, liquidity_usd=20000.0, trade_count_24h=20,
        last_trade_at=datetime.now(timezone.utc),
        raw={"market": {"question": "Will X happen?"}, "book": {}, "trades": []})


def test_run_polymarket_cycle_persists_snapshot_for_each_market(monkeypatch, tmp_path):
    monkeypatch.setattr(polymarket, "POLYMARKET_ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(
        polymarket, "fetch_market_snapshot",
        lambda market, **k: _good_snapshot(market["conditionId"]))
    # Standard-write_raw_snapshot verwendet das Default-Argument (Modulkonstante) --
    # der Scheduler ruft ohne base_dir auf, daher zusätzlich die Signatur umleiten.
    monkeypatch.setattr(
        polymarket, "write_raw_snapshot",
        lambda cid, fetched_at, payload, base_dir=None: str(tmp_path / f"{cid}.json"))

    stored = polymarket_scheduler.run_polymarket_cycle([_market("0xabc123"), _market("0xdef456")])

    assert stored == 2
    markets = db.list_polymarket_markets()
    assert {m["condition_id"] for m in markets} == {"0xabc123", "0xdef456"}
    for cid in ("0xabc123", "0xdef456"):
        rows = db.list_polymarket_snapshots(cid)
        assert len(rows) == 1
        assert bool(rows[0]["usable"]) is True


def test_run_polymarket_cycle_rejects_thin_market_but_still_persists_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(polymarket, "POLYMARKET_ARCHIVE_DIR", tmp_path)
    thin = polymarket.MarketSnapshot(
        condition_id="0xabc123", fetched_at=datetime.now(timezone.utc),
        bid=0.40, ask=0.41, depth_bid_usd=1.0, depth_ask_usd=1.0,   # zu duenn
        volume_24h_usd=5000.0, liquidity_usd=20000.0, trade_count_24h=20,
        last_trade_at=datetime.now(timezone.utc), raw={"market": {}, "book": {}, "trades": []})
    monkeypatch.setattr(polymarket, "fetch_market_snapshot", lambda market, **k: thin)
    monkeypatch.setattr(
        polymarket, "write_raw_snapshot",
        lambda cid, fetched_at, payload, base_dir=None: str(tmp_path / f"{cid}.json"))

    stored = polymarket_scheduler.run_polymarket_cycle([_market()])
    assert stored == 1
    row = db.list_polymarket_snapshots("0xabc123")[0]
    assert bool(row["usable"]) is False
    assert row["reject_code"] == "depth_thin"


def test_run_polymarket_cycle_skips_one_failing_market_and_continues(monkeypatch, tmp_path):
    monkeypatch.setattr(polymarket, "POLYMARKET_ARCHIVE_DIR", tmp_path)

    def fake_snapshot(market, **k):
        if market["conditionId"] == "0xbad":
            raise polymarket.PolymarketAPIError("boom")
        return _good_snapshot(market["conditionId"])

    monkeypatch.setattr(polymarket, "fetch_market_snapshot", fake_snapshot)
    monkeypatch.setattr(
        polymarket, "write_raw_snapshot",
        lambda cid, fetched_at, payload, base_dir=None: str(tmp_path / f"{cid}.json"))

    stored = polymarket_scheduler.run_polymarket_cycle(
        [_market("0xbad"), _market("0xgood")])

    assert stored == 1
    assert db.list_polymarket_snapshots("0xgood")
    assert db.list_polymarket_snapshots("0xbad") == []


def test_run_polymarket_cycle_returns_zero_when_market_discovery_fails(monkeypatch):
    def boom(*a, **k):
        raise polymarket.PolymarketAPIError("kein Netz")

    monkeypatch.setattr(polymarket, "fetch_markets", boom)
    assert polymarket_scheduler.run_polymarket_cycle() == 0


def test_run_polymarket_cycle_skips_market_without_condition_id(monkeypatch, tmp_path):
    monkeypatch.setattr(polymarket, "POLYMARKET_ARCHIVE_DIR", tmp_path)
    stored = polymarket_scheduler.run_polymarket_cycle([{"question": "no condition id"}])
    assert stored == 0
    assert db.list_polymarket_markets() == []
