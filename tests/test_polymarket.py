"""Tests für den Polymarket-IO-Client (PM-0) — vollständig offline: `_get`/`requests.get`
werden gemockt (Muster: `tests/test_universes.py`, `FETCHERS`-Monkeypatch). Kein Test
spricht das echte Netz an.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.research import polymarket as pm


class _FrozenDatetime(datetime):
    """Feste 'jetzt'-Zeit für Tests, die 24h-Fensterung brauchen (BLOCKING 2) — ohne neue
    Test-Dependency (kein freezegun): Subclass von ``datetime``, überschreibt nur ``now()``.
    ``fromtimestamp``/``fromisoformat`` bleiben geerbt und verhalten sich normal."""
    _fixed = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


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
    "volume24hr": 2730.66,
}

# Gegenläufig sortiertes Orderbuch, wie es die CLOB API real liefert (siehe Review-Befund
# BLOCKING 1, verifiziert am 2026-08-13 gegen den echten `book`-Endpunkt): bids aufsteigend
# (schlechtestes zuerst, bestes Gebot am ENDE), asks absteigend (schlechtester zuerst, bester
# Ask am ENDE). Beide Seiten mit mehreren Stufen, damit ein naiver `levels[0]`-Zugriff sicher
# den falschen Preis liefert.
REAL_BOOK = {
    "bids": [
        {"price": "0.001", "size": "10609927.01"},
        {"price": "0.010", "size": "50000.00"},
        {"price": "0.046", "size": "1530.68"},
    ],
    "asks": [
        {"price": "0.999", "size": "1502654.22"},
        {"price": "0.500", "size": "8000.00"},
        {"price": "0.047", "size": "11551.49"},
    ],
    "asset_id": "111111",
    "hash": "0xdeadbeef",
    "last_trade_price": "0.046",
    "market": "0xabc123",
    "min_order_size": "5",
    "neg_risk": False,
    "tick_size": "0.001",
    "timestamp": "1786660464000",
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
    monkeypatch.setattr(pm, "datetime", _FrozenDatetime)
    now = _FrozenDatetime._fixed
    trades = [
        {"size": "100", "price": "0.046", "timestamp": (now - timedelta(hours=1)).timestamp()},
        {"size": "50", "price": "0.047", "timestamp": (now - timedelta(hours=2)).timestamp()},
        # >24h alt -> zählt NICHT in trade_count_24h (Review-Befund BLOCKING 2)
        {"size": "20", "price": "0.05", "timestamp": (now - timedelta(hours=30)).timestamp()},
    ]

    def fake_get(base_url, path, params=None, **kwargs):
        if base_url == pm.CLOB_API_BASE:
            return REAL_BOOK
        if base_url == pm.DATA_API_BASE:
            return trades
        raise AssertionError(f"unerwarteter Aufruf: {base_url}{path}")

    monkeypatch.setattr(pm, "_get", fake_get)
    snapshot = pm.fetch_market_snapshot(GAMMA_MARKET)

    assert snapshot.condition_id == "0xabc123"
    # Bestes Gebot/bester Ask liegen bei REAL_BOOK am ENDE der jeweiligen Liste, nicht bei
    # levels[0] (0.001 / 0.999) -- Review-Befund BLOCKING 1.
    assert snapshot.bid == pytest.approx(0.046)
    assert snapshot.ask == pytest.approx(0.047)
    # Kumulierte Tiefe im 10%-Fenster um mid=0.0465 -- nur die jeweils beste Stufe liegt
    # innerhalb, die dicken Stufen bei 0.001/0.999 fallen raus (Review-Befund IMPORTANT 4).
    assert snapshot.depth_bid_usd == pytest.approx(0.046 * 1530.68)
    assert snapshot.depth_ask_usd == pytest.approx(0.047 * 11551.49)
    # volume_24h_usd kommt aus Gammas volume24hr, NICHT aus der (ungefensterten) Trade-Summe
    # (Review-Befund BLOCKING 2) -- Summe aller drei Trade-`size`-Werte wäre 170.0.
    assert snapshot.volume_24h_usd == pytest.approx(2730.66)
    # trade_count_24h zählt nur die beiden Trades <24h alt, nicht den 30h alten.
    assert snapshot.trade_count_24h == 2
    assert snapshot.last_trade_at == now - timedelta(hours=1)
    assert snapshot.raw["market"] is GAMMA_MARKET
    assert snapshot.raw["book"] is REAL_BOOK
    # question/resolution_rules bleiben im Rohdaten-Bundle unverändert erhalten
    assert snapshot.raw["market"]["question"] == GAMMA_MARKET["question"]
    assert snapshot.raw["market"]["description"] == GAMMA_MARKET["description"]


def test_fetch_market_snapshot_falls_back_to_trade_sum_when_gamma_volume_missing(monkeypatch):
    market = {k: v for k, v in GAMMA_MARKET.items() if k != "volume24hr"}
    monkeypatch.setattr(pm, "datetime", _FrozenDatetime)
    now = _FrozenDatetime._fixed
    trades = [
        {"size": 65.87, "price": 0.046, "timestamp": (now - timedelta(hours=1)).timestamp()},
        {"size": 10.0, "price": 0.50, "timestamp": (now - timedelta(hours=2)).timestamp()},
    ]

    def fake_get(base_url, path, params=None, **kwargs):
        if base_url == pm.CLOB_API_BASE:
            return {"bids": [], "asks": []}
        if base_url == pm.DATA_API_BASE:
            return trades
        raise AssertionError(f"unerwarteter Aufruf: {base_url}{path}")

    monkeypatch.setattr(pm, "_get", fake_get)
    snapshot = pm.fetch_market_snapshot(market)
    # size * price je Trade, NICHT size allein (Review-Befund BLOCKING 2: size sind Shares).
    expected = 65.87 * 0.046 + 10.0 * 0.50
    assert snapshot.volume_24h_usd == pytest.approx(expected)


def test_fetch_market_snapshot_computes_cumulative_depth_within_window(monkeypatch):
    book = {
        "bids": [
            {"price": "0.20", "size": "1000"},   # außerhalb 10%-Fenster unter mid -> raus
            {"price": "0.38", "size": "200"},    # innerhalb
            {"price": "0.40", "size": "300"},    # bestes Gebot, innerhalb
        ],
        "asks": [
            {"price": "0.60", "size": "500"},    # außerhalb 10%-Fenster über mid -> raus
            {"price": "0.43", "size": "150"},    # innerhalb
            {"price": "0.42", "size": "250"},    # bester Ask, innerhalb
        ],
    }

    def fake_get(base_url, path, params=None, **kwargs):
        if base_url == pm.CLOB_API_BASE:
            return book
        if base_url == pm.DATA_API_BASE:
            return []
        raise AssertionError(f"unerwarteter Aufruf: {base_url}{path}")

    monkeypatch.setattr(pm, "_get", fake_get)
    snapshot = pm.fetch_market_snapshot(GAMMA_MARKET)

    assert snapshot.bid == pytest.approx(0.40)
    assert snapshot.ask == pytest.approx(0.42)
    # mid=0.41, Fenster 10% -> bid>=0.369, ask<=0.451: je äußerste Stufe fällt raus.
    assert snapshot.depth_bid_usd == pytest.approx(0.38 * 200 + 0.40 * 300)
    assert snapshot.depth_ask_usd == pytest.approx(0.43 * 150 + 0.42 * 250)
    # Die beste Stufe allein wäre deutlich kleiner -- genau das ist der Punkt des Fixes.
    assert snapshot.depth_bid_usd > 0.40 * 300
    assert snapshot.depth_ask_usd > 0.42 * 250


def test_fetch_market_snapshot_sanitizes_trades_in_raw_bundle(monkeypatch):
    trade = {
        "asset": "111111", "conditionId": "0xabc123", "side": "SELL", "size": 65.87,
        "price": 0.046, "timestamp": 1786660464, "outcome": "Yes", "outcomeIndex": 0,
        "transactionHash": "0xdeadbeef",
        # personenbezogene Data-API-Felder, die das Archiv nicht braucht (IMPORTANT 5):
        "proxyWallet": "0xwallet", "pseudonym": "AnonBear", "name": "Real Name",
        "bio": "trader bio", "profileImage": "http://x/img.png",
        "profileImageOptimized": "http://x/img_opt.png", "icon": "http://x/icon.png",
        "eventSlug": "some-event", "slug": "some-slug", "title": "Some title",
    }

    def fake_get(base_url, path, params=None, **kwargs):
        if base_url == pm.CLOB_API_BASE:
            return {"bids": [], "asks": []}
        if base_url == pm.DATA_API_BASE:
            return [trade]
        raise AssertionError(f"unerwarteter Aufruf: {base_url}{path}")

    monkeypatch.setattr(pm, "_get", fake_get)
    snapshot = pm.fetch_market_snapshot(GAMMA_MARKET)

    archived = snapshot.raw["trades"][0]
    assert set(archived) == {
        "conditionId", "asset", "side", "outcome", "outcomeIndex", "price", "size",
        "timestamp", "transactionHash"}
    for pii_key in ("proxyWallet", "pseudonym", "name", "bio", "profileImage",
                    "profileImageOptimized", "icon", "eventSlug", "slug", "title"):
        assert pii_key not in archived
    # Markt-Rohdaten (question/description) bleiben unverändert vollständig.
    assert snapshot.raw["market"]["question"] == GAMMA_MARKET["question"]


def test_fetch_market_snapshot_survives_book_failure(monkeypatch):
    def fake_get(base_url, path, params=None, **kwargs):
        if base_url == pm.CLOB_API_BASE:
            raise pm.PolymarketAPIError("boom")
        return []

    monkeypatch.setattr(pm, "_get", fake_get)
    snapshot = pm.fetch_market_snapshot(GAMMA_MARKET)
    assert snapshot.bid is None and snapshot.ask is None
    assert snapshot.condition_id == "0xabc123"   # Rest des Snapshots bleibt nutzbar


# ── _parse_book_levels / _book_best_price / _book_depth_usd (BLOCKING 1, IMPORTANT 4) ──────

def test_parse_book_levels_returns_empty_for_invalid_input():
    assert pm._parse_book_levels([]) == []
    assert pm._parse_book_levels(None) == []
    assert pm._parse_book_levels("not-a-list") == []


def test_book_best_price_none_for_empty_levels():
    assert pm._book_best_price([], side="bid") is None
    assert pm._book_best_price([], side="ask") is None


def test_book_best_price_picks_max_for_bid_and_min_for_ask_regardless_of_order():
    # REAL_BOOK ist gegenläufig sortiert: levels[0] wäre für Bids das SCHLECHTESTE Gebot
    # (0.001) und für Asks der SCHLECHTESTE Ask (0.999). Die robuste max()/min()-Bestimmung
    # muss unabhängig von der Sortierung das jeweils beste Level finden.
    bid_levels = pm._parse_book_levels(REAL_BOOK["bids"])
    ask_levels = pm._parse_book_levels(REAL_BOOK["asks"])
    assert pm._book_best_price(bid_levels, side="bid") == pytest.approx(0.046)
    assert pm._book_best_price(ask_levels, side="ask") == pytest.approx(0.047)


def test_book_depth_usd_sums_only_levels_within_window():
    bid_levels = [(0.20, 1000.0), (0.38, 200.0), (0.40, 300.0)]
    depth_bid = pm._book_depth_usd(bid_levels, side="bid", mid=0.41, window_pct=10.0)
    assert depth_bid == pytest.approx(0.38 * 200 + 0.40 * 300)

    ask_levels = [(0.60, 500.0), (0.43, 150.0), (0.42, 250.0)]
    depth_ask = pm._book_depth_usd(ask_levels, side="ask", mid=0.41, window_pct=10.0)
    assert depth_ask == pytest.approx(0.43 * 150 + 0.42 * 250)


# ── Trade-Hilfsfunktionen (BLOCKING 2) ──────────────────────────────────────────

def test_trade_amount_usd_multiplies_size_by_price():
    assert pm._trade_amount_usd({"size": 65.87, "price": 0.046}) == pytest.approx(65.87 * 0.046)
    assert pm._trade_amount_usd({"size": "65.87", "price": "0.046"}) == pytest.approx(65.87 * 0.046)


def test_trade_amount_usd_none_when_field_missing():
    assert pm._trade_amount_usd({"size": 10}) is None
    assert pm._trade_amount_usd({"price": 0.5}) is None


def test_trade_count_24h_filters_by_window():
    now = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
    trades = [
        {"timestamp": (now - timedelta(hours=1)).timestamp()},
        {"timestamp": (now - timedelta(hours=23, minutes=59)).timestamp()},
        {"timestamp": (now - timedelta(hours=25)).timestamp()},   # zu alt
        {"timestamp": None},                                      # unparsbar -> nicht gezählt
    ]
    assert pm._trade_count_24h(trades, now=now) == 2


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
