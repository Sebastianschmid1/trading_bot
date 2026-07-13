"""RES-001: pure shadow signal, simulation, isolation, and OMS boundary tests."""

from datetime import datetime, timezone

import pytest

from stockbot.core.domain import Mode, Signal, SignalStatus, TradeIntent
from stockbot.core.market_data import Quote
from stockbot.core.mode_report import build_mode_report
from stockbot.execution.oms import OMS
from stockbot.research.shadow import (
    shadow_performance_snapshot,
    simulate_entry,
    simulate_exit,
    to_shadow_signal,
)


def _strategy_result():
    return {
        "ticker": "AAPL",
        "direction": "long",
        "price": 100.0,
        "as_of": "2026-07-13T10:00:00Z",
        "stop_loss": 95.0,
        "take_profit": 110.0,
    }


def _shadow_signal(**kwargs):
    return to_shadow_signal(_strategy_result(), strategy_version_id=7, signal_id=17, **kwargs)


def test_to_shadow_signal_sets_mode_and_allowed_statuses():
    generated = _shadow_signal()
    published = _shadow_signal(
        status=SignalStatus.PUBLISHED,
        published_at="2026-07-13T10:01:00Z",
    )

    assert generated.mode is Mode.SHADOW
    assert generated.status is SignalStatus.GENERATED
    assert generated.strategy_version_id == 7
    assert generated.data_exchange_time == "2026-07-13T10:00:00Z"
    assert published.status is SignalStatus.PUBLISHED
    with pytest.raises(ValueError, match="only be created"):
        _shadow_signal(status=SignalStatus.ACCEPTED)


def test_domain_signal_is_recast_without_mutating_original():
    original = Signal(
        id=4, strategy_version_id=2, ticker="MSFT", direction="long",
        mode=Mode.PAPER, status=SignalStatus.PUBLISHED,
    )

    shadow = to_shadow_signal(original)

    assert shadow.mode is Mode.SHADOW and shadow.status is SignalStatus.GENERATED
    assert original.mode is Mode.PAPER and original.status is SignalStatus.PUBLISHED


def test_simulate_entry_with_and_without_adverse_slippage():
    signal = _shadow_signal()
    quote = Quote(
        ticker="AAPL", price=100.0,
        as_of=datetime(2026, 7, 13, 10, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 7, 13, 10, tzinfo=timezone.utc),
        provider="test",
    )

    plain = simulate_entry(signal, quote_or_price=quote, qty=2)
    slipped = simulate_entry(signal, quote_or_price=100.0, slippage_bps=25, qty=2)

    assert plain.price == 100.0
    assert plain.mode is Mode.SHADOW
    assert slipped.reference_price == 100.0
    assert slipped.price == pytest.approx(100.25)


def test_simulate_exit_uses_stop_before_target_when_both_hit_same_bar():
    entry = simulate_entry(_shadow_signal(), quote_or_price=100.0, qty=2)

    result = simulate_exit(
        entry,
        bars_or_price=[{
            "timestamp": "2026-07-14T20:00:00Z",
            "low": 94.0,
            "high": 111.0,
            "close": 105.0,
        }],
        stop_loss=95.0,
        take_profit=110.0,
    )

    assert result.reason == "stop_loss"
    assert result.price == 95.0
    assert result.pnl == -10.0
    assert result.bar_index == 0


def test_shadow_chain_builds_report_and_rejects_paper_mixing():
    signal = _shadow_signal(status="published")
    entry = simulate_entry(signal, quote_or_price=100.0, slippage_bps=10, qty=3)
    exit_result = simulate_exit(
        entry,
        bars_or_price=[{
            "timestamp": "2026-07-14T20:00:00Z",
            "low": 99.0,
            "high": 112.0,
            "close": 111.0,
        }],
        stop_loss=95.0,
        take_profit=110.0,
    )
    snapshot = shadow_performance_snapshot([entry], [exit_result])

    report = build_mode_report(Mode.SHADOW, performance_snapshots=[snapshot])

    assert snapshot.mode is Mode.SHADOW
    assert report.mode is Mode.SHADOW
    assert report.net_pnl == pytest.approx((110.0 - 100.1) * 3)
    assert report.trade_count == 1
    assert report.strategy_versions == [7]
    with pytest.raises(ValueError, match="does not match report mode"):
        build_mode_report(Mode.PAPER, performance_snapshots=[snapshot])


class _NoPersistenceWrites:
    def get_order_by_idempotency_key(self, key):
        return None


class _NoBrokerCalls:
    def __init__(self):
        self.submissions = []

    def submit_buy(self, ticker, **kwargs):
        self.submissions.append((ticker, kwargs))
        raise AssertionError("SHADOW signal reached broker")


def test_oms_rejects_shadow_signal_as_paper_only_before_order_creation():
    signal = _shadow_signal(status=SignalStatus.PUBLISHED)
    broker = _NoBrokerCalls()
    intent = TradeIntent(
        user_id=42,
        signal_id=signal.id,
        requested_action="accept",
        accepted_exit_policy="strategy-default",
        source_channel="telegram",
        created_at=datetime.now(timezone.utc).isoformat(),
        idempotency_key="shadow:42:17",
        mode=Mode.SHADOW,
    )

    result = OMS(
        signal_loader=lambda signal_id: signal,
        broker_client=broker,
        persistence=_NoPersistenceWrites(),
    ).submit_intent(intent, price=100.0, trade_size=1000.0)

    assert result.ok is False
    assert result.code == "paper_only"
    assert result.order is None
    assert broker.submissions == []
