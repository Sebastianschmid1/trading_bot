"""Tests für den Polymarket-IO-Client (PM-0) — vollständig offline: `_get`/`requests.get`
werden gemockt (Muster: `tests/test_universes.py`, `FETCHERS`-Monkeypatch). Kein Test
spricht das echte Netz an.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stockbot.research import polymarket as pm


GAMMA_MARKET = {
    "conditionId": "0xabc123",
    "clobTokenIds": '["111111", "222222"]',
    "slug": "will-x-happen",
    "question": "Will X happen by 2026-12-31?",
    "resolutionSource": "https://example.org/rules",
    "description": "Resolves YES if X happens before the end date, per official sources.",
    "endDate": "2026-12-31T00:00:00Z",
    "category": "Politics",
    "liquidity": "12345.67",
}


# ── fetch_markets / parse_market_info ────────────────────────────────────────

def test_fetch_markets_calls_gamma_api_and_returns_list(monkeypatch):
    captured = {}

    def fake_get(base_url, path, params=None, **kwargs):
        captured["base_url"] = base_url
        captured["path"] = path
        captured["params"] = params
        return [GAMMA_MARKET]

    monkeypatch.setattr(pm, "_get", fake_get)
    markets = pm.fetch_markets()
    assert markets == [GAMMA_MARKET]
    assert captured["base_url"] == pm.GAMMA_API_BASE
    assert captured["path"] == "/markets"


def test_fetch_markets_unwraps_data_envelope(monkeypatch):
    monkeypatch.setattr(pm, "_get", lambda *a, **k: {"data": [GAMMA_MARKET]})
    assert pm.fetch_markets() == [GAMMA_MARKET]


def test_parse_market_info_extracts_fields():
    info = pm.parse_market_info(GAMMA_MARKET)
    assert info.condition_id == "0xabc123"
    assert info.token_id == "111111"
    assert info.slug == "will-x-happen"
    assert info.question.startswith("Will X happen")
    assert info.resolution_source == "https://example.org/rules"
    assert "Resolves YES" in info.resolution_rules
    assert info.end_date == "2026-12-31T00:00:00Z"
    assert info.category == "Politics"


def test_parse_market_info_tolerates_missing_fields():
    info = pm.parse_market_info({})
    assert info.condition_id == ""
    assert info.token_id == ""
    assert info.question == ""
    assert info.end_date is None


def test_first_token_id_accepts_list_of_dicts():
    market = {"tokens": [{"token_id": "999"}, {"token_id": "888"}]}
    info = pm.parse_market_info(market)
    assert info.token_id == "999"


# ── fetch_book / fetch_market_snapshot ───────────────────────────────────────

def test_fetch_book_calls_clob_api(monkeypatch):
    captured = {}
    monkeypatch.setattr(pm, "_get", lambda base, path, params=None, **k:
                        captured.update(base_url=base, path=path, params=params) or {"bids": []})
    pm.fetch_book("111111")
    assert captured["base_url"] == pm.CLOB_API_BASE
    assert captured["path"] == "/book"
    assert captured["params"] == {"token_id": "111111"}


def test_fetch_trades_calls_data_api(monkeypatch):
    captured = {}
    monkeypatch.setattr(pm, "_get", lambda base, path, params=None, **k:
                        captured.update(base_url=base, path=path, params=params) or [])
    pm.fetch_trades("0xabc123")
    assert captured["base_url"] == pm.DATA_API_BASE
    assert captured["path"] == "/trades"
    assert captured["params"]["market"] == "0xabc123"


def test_fetch_market_snapshot_combines_book_and_trades(monkeypatch):
    book = {"bids": [{"price": "0.40", "size": "500"}],
            "asks": [{"price": "0.42", "size": "300"}]}
    trades = [
        {"size": "100", "timestamp": "2026-08-13T10:00:00Z"},
        {"size": "50", "timestamp": "2026-08-13T11:00:00Z"},
    ]

    def fake_get(base_url, path, params=None, **kwargs):
        if base_url == pm.CLOB_API_BASE:
            return book
        if base_url == pm.DATA_API_BASE:
            return trades
        raise AssertionError(f"unerwarteter Aufruf: {base_url}{path}")

    monkeypatch.setattr(pm, "_get", fake_get)
    snapshot = pm.fetch_market_snapshot(GAMMA_MARKET)

    assert snapshot.condition_id == "0xabc123"
    assert snapshot.bid == 0.40
    assert snapshot.ask == 0.42
    assert snapshot.depth_bid_usd == pytest.approx(0.40 * 500)
    assert snapshot.depth_ask_usd == pytest.approx(0.42 * 300)
    assert snapshot.volume_24h_usd == pytest.approx(150.0)
    assert snapshot.trade_count_24h == 2
    assert snapshot.last_trade_at == datetime(2026, 8, 13, 11, 0, tzinfo=timezone.utc)
    assert snapshot.raw["market"] is GAMMA_MARKET
    assert snapshot.raw["book"] is book
    assert snapshot.raw["trades"] is trades
    # question/resolution_rules bleiben im Rohdaten-Bundle unverändert erhalten
    assert snapshot.raw["market"]["question"] == GAMMA_MARKET["question"]
    assert snapshot.raw["market"]["description"] == GAMMA_MARKET["description"]


def test_fetch_market_snapshot_survives_book_failure(monkeypatch):
    def fake_get(base_url, path, params=None, **kwargs):
        if base_url == pm.CLOB_API_BASE:
            raise pm.PolymarketAPIError("boom")
        return []

    monkeypatch.setattr(pm, "_get", fake_get)
    snapshot = pm.fetch_market_snapshot(GAMMA_MARKET)
    assert snapshot.bid is None and snapshot.ask is None
    assert snapshot.condition_id == "0xabc123"   # Rest des Snapshots bleibt nutzbar


def test_book_top_returns_none_for_empty_levels():
    assert pm._book_top([]) == (None, None)
    assert pm._book_top(None) == (None, None)


# ── _get: Retry-Verhalten (kein Endlos-Retry) ────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_get_retries_once_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)

    def fake_requests_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(429, headers={"Retry-After": "1"})
        return _FakeResponse(200, payload={"ok": True})

    monkeypatch.setattr(pm.requests, "get", fake_requests_get)
    result = pm._get(pm.GAMMA_API_BASE, "/markets")
    assert result == {"ok": True}
    assert calls["n"] == 2


def test_get_raises_after_one_retry_still_failing(monkeypatch):
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.setattr(pm.requests, "get", lambda *a, **k: _FakeResponse(503))
    with pytest.raises(pm.PolymarketAPIError):
        pm._get(pm.GAMMA_API_BASE, "/markets")


def test_get_does_not_retry_on_plain_4xx(monkeypatch):
    calls = {"n": 0}

    def fake_requests_get(*a, **k):
        calls["n"] += 1
        return _FakeResponse(404)

    monkeypatch.setattr(pm.requests, "get", fake_requests_get)
    with pytest.raises(pm.PolymarketAPIError):
        pm._get(pm.GAMMA_API_BASE, "/markets/does-not-exist")
    assert calls["n"] == 1   # kein Retry auf einen regulären Client-Fehler


# ── Rohdatenarchiv (JSON) ─────────────────────────────────────────────────────

def test_raw_json_path_partitions_by_condition_and_date(tmp_path):
    fetched_at = datetime(2026, 8, 13, 14, 30, 5, tzinfo=timezone.utc)
    path = pm.raw_json_path("0xabc123", fetched_at, base_dir=tmp_path)
    assert path == tmp_path / "0xabc123" / "2026-08-13" / "143005.json"


def test_write_raw_snapshot_persists_full_payload(tmp_path):
    fetched_at = datetime(2026, 8, 13, 14, 30, 5, tzinfo=timezone.utc)
    payload = {"market": GAMMA_MARKET, "book": {"bids": []}, "trades": []}
    file_path = pm.write_raw_snapshot("0xabc123", fetched_at, payload, base_dir=tmp_path)

    assert Path(file_path).exists()
    on_disk = json.loads(Path(file_path).read_text(encoding="utf-8"))
    assert on_disk["market"]["question"] == GAMMA_MARKET["question"]
    assert on_disk["market"]["description"] == GAMMA_MARKET["description"]


def test_write_raw_snapshot_overwrites_same_partition(tmp_path):
    fetched_at = datetime(2026, 8, 13, 14, 30, 5, tzinfo=timezone.utc)
    pm.write_raw_snapshot("0xabc123", fetched_at, {"n": 1}, base_dir=tmp_path)
    file_path = pm.write_raw_snapshot("0xabc123", fetched_at, {"n": 2}, base_dir=tmp_path)
    on_disk = json.loads(Path(file_path).read_text(encoding="utf-8"))
    assert on_disk == {"n": 2}
