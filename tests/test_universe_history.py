"""Tests für Point-in-Time-Universen + Survivorship-Messung (W5.4, Gate P7)."""

from stockbot.backtest.universe_history import (
    PointInTimeUniverse,
    UniverseSnapshot,
    measure_survivorship_bias,
    snapshot_version,
)


def test_snapshot_version_is_deterministic_and_order_invariant():
    a = snapshot_version("2020-01-01", ["AAPL", "MSFT"])
    b = snapshot_version("2020-01-01", ["msft", " aapl "])  # Reihenfolge/Case/Whitespace egal
    assert a == b
    assert snapshot_version("2021-01-01", ["AAPL", "MSFT"]) != a  # Stichtag zählt


def test_snapshot_normalises_members_and_keeps_delisted_disjoint():
    snap = UniverseSnapshot("2020-06-30", members=[" aapl ", "MSFT", "msft"],
                            delisted=["MSFT", "ENRN"])
    assert snap.members == frozenset({"AAPL", "MSFT"})
    assert snap.delisted == frozenset({"ENRN"})  # delisted schließt aktive Mitglieder aus
    assert snap.as_dict()["n_members"] == 2


def test_members_as_of_picks_latest_snapshot_not_after_date():
    u = PointInTimeUniverse([
        UniverseSnapshot("2020-01-01", ["A", "B"]),
        UniverseSnapshot("2021-01-01", ["A", "C"]),
        UniverseSnapshot("2022-01-01", ["A", "C", "D"]),
    ])
    assert u.members_as_of("2019-12-31") == frozenset()          # vor der ersten Aufnahme
    assert u.members_as_of("2020-06-30") == frozenset({"A", "B"})  # jüngste ≤ Datum
    assert u.members_as_of("2021-01-01") == frozenset({"A", "C"})  # exakt am Stichtag
    assert u.members_as_of("2025-01-01") == frozenset({"A", "C", "D"})
    assert len(u.versions()) == 3


def test_point_in_time_universe_add_keeps_order():
    u = PointInTimeUniverse()
    u.add(UniverseSnapshot("2022-01-01", ["X"]))
    u.add(UniverseSnapshot("2020-01-01", ["Y"]))
    assert [s.as_of for s in u.snapshots] == ["2020-01-01", "2022-01-01"]
    assert u.members_as_of("2021-01-01") == frozenset({"Y"})


def test_measure_survivorship_bias_flags_removed_and_added():
    # damals im Index, heute delisted → verzerrt einen naiven „heute"-Backtest
    report = measure_survivorship_bias(
        current_members=["AAPL", "MSFT", "NVDA"],
        historical_members=["AAPL", "MSFT", "ENRN", "LEH"],  # ENRN/LEH später delistet
    )
    assert report.removed_since == ("ENRN", "LEH")
    assert report.added_since == ("NVDA",)
    assert report.historical_count == 4
    assert report.survivorship_bias_pct == 50.0  # 2 von 4 fehlen im naiven Backtest


def test_measure_survivorship_bias_empty_historical_is_zero():
    report = measure_survivorship_bias(current_members=["A"], historical_members=[])
    assert report.survivorship_bias_pct == 0.0
    assert report.removed_since == ()
