"""Tests für den reinen Polymarket-Qualitäts-/Liquiditätsfilter (PM-0).

IO-frei per Definition des Moduls — keine Mocks, keine Netzwerksperre nötig.
"""

from datetime import datetime, timedelta, timezone

import pytest

from stockbot.research import polymarket_quality as pq


def _now():
    return datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def _good_kwargs(**overrides):
    kwargs = dict(
        bid=0.40, ask=0.42,
        depth_bid_usd=1000.0, depth_ask_usd=1000.0,
        volume_24h_usd=5000.0, trade_count_24h=20,
        fetched_at=_now(), end_date=_now() + timedelta(days=30),
        history=(), thresholds=pq.DEFAULT_THRESHOLDS, now=_now(),
    )
    kwargs.update(overrides)
    return kwargs


# ── compute_mid / compute_spread_bps ─────────────────────────────────────────

def test_compute_mid_averages_bid_ask():
    assert pq.compute_mid(0.40, 0.42) == pytest.approx(0.41)


def test_compute_mid_none_when_side_missing():
    assert pq.compute_mid(None, 0.42) is None
    assert pq.compute_mid(0.40, None) is None


def test_compute_spread_bps():
    # (0.42 - 0.40) / 0.40 * 10_000 = 500 bps
    assert pq.compute_spread_bps(0.40, 0.42) == pytest.approx(500.0)


def test_compute_spread_bps_none_without_bid():
    assert pq.compute_spread_bps(None, 0.42) is None
    assert pq.compute_spread_bps(0.0, 0.42) is None


# ── evaluate_snapshot: der usable-Fall ───────────────────────────────────────

def test_evaluate_snapshot_usable_when_all_gates_pass():
    state = pq.evaluate_snapshot(**_good_kwargs())
    assert state.usable is True
    assert state.reject_code == ""
    assert state.probability == pq.compute_mid(0.40, 0.42)
    assert state.delta == {"1h": None, "6h": None, "24h": None}   # keine Historie -> kein Delta


# ── Ablehnungsgründe (Acceptance Criterion 4) ────────────────────────────────

def test_rejects_empty_book_no_bid():
    state = pq.evaluate_snapshot(**_good_kwargs(bid=None))
    assert state.usable is False
    assert state.reject_code == "book_empty"


def test_rejects_empty_book_no_ask():
    state = pq.evaluate_snapshot(**_good_kwargs(ask=None))
    assert state.usable is False
    assert state.reject_code == "book_empty"


def test_rejects_wide_spread():
    # bid=0.10, ask=0.42 -> Spread weit über dem Default-Limit von 500 bps
    state = pq.evaluate_snapshot(**_good_kwargs(bid=0.10, ask=0.42))
    assert state.usable is False
    assert state.reject_code == "spread_wide"


def test_rejects_thin_depth():
    state = pq.evaluate_snapshot(**_good_kwargs(depth_bid_usd=10.0, depth_ask_usd=10.0))
    assert state.usable is False
    assert state.reject_code == "depth_thin"


def test_rejects_low_volume():
    state = pq.evaluate_snapshot(**_good_kwargs(volume_24h_usd=1.0))
    assert state.usable is False
    assert state.reject_code == "volume_low"


def test_rejects_few_trades():
    state = pq.evaluate_snapshot(**_good_kwargs(trade_count_24h=1))
    assert state.usable is False
    assert state.reject_code == "trades_few"


def test_rejects_stale_snapshot():
    old_fetch = _now() - timedelta(hours=3)
    state = pq.evaluate_snapshot(**_good_kwargs(fetched_at=old_fetch, now=_now()))
    assert state.usable is False
    assert state.reject_code == "snapshot_stale"


def test_rejects_market_near_resolution():
    # Restlaufzeit unter dem Default-Minimum von 24h
    state = pq.evaluate_snapshot(**_good_kwargs(end_date=_now() + timedelta(hours=1)))
    assert state.usable is False
    assert state.reject_code == "near_resolution"


def test_market_without_end_date_is_not_rejected_for_resolution():
    state = pq.evaluate_snapshot(**_good_kwargs(end_date=None))
    assert state.usable is True


# ── Δp über 1h/6h/24h aus der Snapshot-Historie ──────────────────────────────

def test_delta_computed_from_history_within_each_window():
    # Referenz je Fenster = die JÜNGSTE Historie, die noch mindestens so alt ist wie das
    # Fenster selbst ("as-of"-Semantik: der letzte bekannte Wert VOR der Fenstergrenze).
    now = _now()
    mid = pq.compute_mid(0.40, 0.42)
    history = [
        {"fetched_at": now - timedelta(minutes=90), "mid": 0.30},    # >=1h alt -> Referenz 1h
        {"fetched_at": now - timedelta(minutes=400), "mid": 0.25},   # >=6h alt -> Referenz 6h
        {"fetched_at": now - timedelta(minutes=1500), "mid": 0.20},  # >=24h alt -> Referenz 24h
    ]
    state = pq.evaluate_snapshot(**_good_kwargs(history=history, now=now))
    assert state.usable is True
    assert state.delta["1h"] == pytest.approx(mid - 0.30)
    assert state.delta["6h"] == pytest.approx(mid - 0.25)
    assert state.delta["24h"] == pytest.approx(mid - 0.20)


def test_delta_none_when_no_snapshot_old_enough_for_any_window():
    now = _now()
    # Einzige Historie ist erst 10 Minuten alt -> zu jung für 1h/6h/24h-Referenzen.
    history = [{"fetched_at": now - timedelta(minutes=10), "mid": 0.20}]
    state = pq.evaluate_snapshot(**_good_kwargs(history=history, now=now))
    assert state.usable is True
    assert state.delta == {"1h": None, "6h": None, "24h": None}


def test_delta_picks_snapshot_closest_to_window_boundary():
    now = _now()
    history = [
        {"fetched_at": now - timedelta(minutes=50), "mid": 0.35},   # innerhalb 1h -> ignoriert
        {"fetched_at": now - timedelta(minutes=70), "mid": 0.30},   # knapp außerhalb 1h -> Referenz
        {"fetched_at": now - timedelta(minutes=110), "mid": 0.10},  # älter, aber weiter weg
    ]
    state = pq.evaluate_snapshot(**_good_kwargs(history=history, now=now))
    assert state.delta["1h"] == pq.compute_mid(0.40, 0.42) - 0.30
