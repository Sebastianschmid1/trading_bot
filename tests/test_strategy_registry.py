"""STRAT-003: unveränderliche Strategie-Snapshots und append-only Promotions."""

import dataclasses

import pytest

from stockbot.core.domain import Mode, Signal, StrategyVersion
from stockbot.core.strategy_registry import StrategyVersionRegistry


def template(strategy_id=7, status="draft", params=None):
    return StrategyVersion(
        id=None,
        strategy_id=strategy_id,
        version=0,
        params=params or {"period": 14},
        feature_version="features-v1",
        universe_version="sp500-v1",
        entry_rules="siehe stockbot.market.strategies::adx_trend_signal",
        exit_rules="ATR-SL/TP; siehe stockbot.market.strategies::adx_trend_signal",
        cost_model={"commission_bps": 1},
        release_status=status,
        code_commit="abc123",
    )


def test_publish_assigns_ids_timestamps_and_consecutive_versions():
    registry = StrategyVersionRegistry()
    first = registry.publish(template())
    second = registry.publish(template())

    assert (first.version, second.version) == (1, 2)
    assert first.id != second.id
    assert first.created_at and second.created_at
    assert registry.latest(7) is second
    assert registry.get(7, 1) is first
    assert registry.all_versions(7) == (first, second)


def test_publish_preserves_explicit_initial_version_but_republish_advances_it():
    registry = StrategyVersionRegistry()
    imported = dataclasses.replace(template(), version=4)

    first = registry.publish(imported)
    second = registry.publish(imported)

    assert (first.version, second.version) == (4, 5)


def test_published_snapshot_is_deeply_immutable_and_has_no_mutation_api():
    source = {"nested": {"windows": [10, 20]}}
    registry = StrategyVersionRegistry()
    published = registry.publish(template(params=source))
    source["nested"]["windows"].append(30)

    assert published.params["nested"]["windows"] == (10, 20)
    with pytest.raises(dataclasses.FrozenInstanceError):
        published.release_status = "live"
    with pytest.raises(TypeError):
        published.params["new"] = True
    for method in ("update", "delete", "remove"):
        assert not hasattr(registry, method)


def test_publish_rejects_unknown_release_status():
    with pytest.raises(ValueError, match="Release-Status"):
        StrategyVersionRegistry().publish(template(status="experimental"))


def test_promote_requires_each_forward_step_and_actor_without_replacing_snapshot():
    registry = StrategyVersionRegistry()
    published = registry.publish(template())

    with pytest.raises(ValueError, match="nicht erlaubt"):
        registry.promote(7, 1, "shadow", actor="owner")
    with pytest.raises(ValueError, match="actor"):
        registry.promote(7, 1, "candidate", actor=" ")

    candidate = registry.promote(7, 1, "candidate", actor="owner@example.test")
    shadow = registry.promote(7, 1, "shadow", actor="owner@example.test")
    assert candidate.release_status == "candidate"
    assert shadow.release_status == "shadow"
    assert registry.get(7, 1) is published
    assert published.release_status == "draft"
    assert registry.effective_status(7, 1) == "shadow"
    with pytest.raises(ValueError, match="nicht erlaubt"):
        registry.promote(7, 1, "candidate", actor="owner")


def test_archive_is_allowed_from_every_active_status_and_is_terminal():
    for initial in ("draft", "candidate", "shadow", "paper", "live"):
        registry = StrategyVersionRegistry()
        registry.publish(template(status=initial))
        archived = registry.promote(7, 1, "archived", actor="risk-owner")
        assert archived.release_status == "archived"
        with pytest.raises(ValueError, match="archiviert"):
            registry.promote(7, 1, "archived", actor="risk-owner")


def test_status_history_is_append_only_and_records_actor():
    registry = StrategyVersionRegistry()
    registry.publish(template())
    registry.promote(7, 1, "candidate", actor="alice")
    first_view = registry.status_history(7, 1)
    registry.promote(7, 1, "shadow", actor="bob")

    assert len(first_view) == 1
    history = registry.status_history(7, 1)
    assert [(event.old_status, event.new_status, event.actor) for event in history] == [
        ("draft", "candidate", "alice"),
        ("candidate", "shadow", "bob"),
    ]


@pytest.mark.parametrize("key", ["standard", "ai_adaptive", "bb_revert"])
def test_snapshot_from_registry_builds_valid_production_template(key):
    snapshot = StrategyVersionRegistry().snapshot_from_registry(key, code_commit="deadbeef")

    assert snapshot.id is None and snapshot.version == 0
    assert snapshot.strategy_id > 0
    assert snapshot.release_status == "draft"
    assert snapshot.feature_version == snapshot.universe_version == "unversioned"
    assert snapshot.code_commit == "deadbeef"
    assert snapshot.entry_rules
    assert snapshot.exit_rules.startswith("siehe stockbot.market.strategies::")
    assert isinstance(snapshot.params, dict)


def test_snapshot_rejects_nonproduction_strategy():
    with pytest.raises(ValueError, match="keine produktive"):
        StrategyVersionRegistry().snapshot_from_registry("adx_trend", code_commit="deadbeef")


def test_signal_can_reference_the_immutable_published_version():
    registry = StrategyVersionRegistry()
    published = registry.publish(template())
    signal = Signal(
        id=None,
        strategy_version_id=published.id,
        ticker="AAPL",
        direction="long",
        mode=Mode.PAPER,
    )

    assert signal.strategy_version_id == registry.get(7, 1).id
