"""Persistente Strategieversionierung + Live-Verdrahtung (W3.3 → Gate P5).

Jedes persistierte Signal referenziert eine unveränderliche, persistente Strategieversion.
"""

import json
import tempfile
from pathlib import Path

import pytest

from stockbot.core import db
from stockbot.core.strategy_registry import StrategyVersionRegistry


@pytest.fixture(autouse=True)
def fresh_db():
    d = tempfile.mkdtemp(prefix="svtest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()
    yield


def _snapshot(key="standard", commit="deadbeef"):
    return StrategyVersionRegistry().snapshot_from_registry(key, code_commit=commit)


def test_publish_is_idempotent_over_identical_content():
    snap = _snapshot()
    first = db.publish_strategy_version("standard", snap)
    second = db.publish_strategy_version("standard", snap)
    assert first == second   # identischer Inhalt → keine neue Version


def test_publish_changed_content_appends_new_version():
    v1 = db.publish_strategy_version("standard", _snapshot(commit="aaa"))
    v2 = db.publish_strategy_version("standard", _snapshot(commit="bbb"))
    assert v1 != v2
    assert db.get_strategy_version(v2)["version"] == 2
    assert db.get_strategy_version(v1)["version"] == 1


def test_get_strategy_version_roundtrips_immutable_snapshot():
    vid = db.publish_strategy_version("bb_revert", _snapshot("bb_revert"))
    stored = db.get_strategy_version(vid)
    assert stored["strategy_key"] == "bb_revert"
    assert stored["release_status"] == "draft"
    assert stored["entry_rules"]
    assert isinstance(stored["params"], dict)
    assert db.get_strategy_version(999999) is None


def test_ensure_publishes_all_production_strategies_and_is_idempotent():
    mapping = db.ensure_strategy_versions_published(code_commit="c1")
    assert set(mapping) == {"standard", "bb_revert", "ai_adaptive"}
    again = db.ensure_strategy_versions_published(code_commit="c1")
    assert again == mapping   # gleicher Commit → dieselben IDs, keine Duplikate


def test_resolve_returns_id_for_production_and_none_for_research():
    assert db.resolve_strategy_version_id("standard") is not None
    assert db.resolve_strategy_version_id("adx_trend") is None   # nicht produktiv


def test_add_pending_stamps_strategy_version_id_into_signal():
    db.get_or_create_user(555, "svuser")
    signal = {"ticker": "AAPL", "direction": "long", "price": 100.0, "strategy": "standard"}
    assert db.add_pending(555, signal, message_id=0) is True

    trade = db.get_trade(555, "AAPL")
    stamped = trade["signal"]["strategy_version_id"]
    assert stamped == db.resolve_strategy_version_id("standard")
    # verweist auf einen echten, unveränderlichen Snapshot
    assert db.get_strategy_version(stamped)["strategy_key"] == "standard"


def test_add_pending_preserves_explicit_strategy_version_id():
    db.get_or_create_user(556, "svuser2")
    signal = {"ticker": "MSFT", "direction": "long", "price": 100.0,
              "strategy": "standard", "strategy_version_id": 4242}
    db.add_pending(556, signal, message_id=0)
    assert db.get_trade(556, "MSFT")["signal"]["strategy_version_id"] == 4242
