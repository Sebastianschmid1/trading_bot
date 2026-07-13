"""
Tests für die Domänenobjekte (PLAT-001, stockbot/core/domain.py, Plan.md §9.2/§9.3/§9.4).

Reine Konstruktionstests + Abgleich der Enum-Werte gegen die in Plan.md wörtlich
vorgegebenen Zustandsmaschinen. Keine DB, kein IO — die Objekte sind noch nicht an
Persistenz gebunden (folgt mit den nächsten Checklisten-Punkten).
"""

import inspect

from stockbot.core import domain


def test_signal_status_matches_plan_state_machine():
    assert [s.value for s in domain.SignalStatus] == [
        "generated", "filtered", "published", "accepted",
        "rejected", "expired", "blocked_by_risk", "order_created",
    ]


def test_order_status_matches_plan_state_machine():
    assert [s.value for s in domain.OrderStatus] == [
        "created", "validated", "submitted", "accepted_by_broker",
        "partially_filled", "filled", "cancel_requested", "cancelled",
        "rejected", "expired",
    ]


def test_position_status_matches_plan_state_machine():
    assert [s.value for s in domain.PositionStatus] == [
        "pending_open", "open", "pending_close", "closed", "reconciliation_required",
    ]


def test_mode_matches_res002():
    assert [m.value for m in domain.Mode] == ["backtest", "shadow", "paper", "live"]


def test_risk_profile_has_all_plan_fields():
    rp = domain.RiskProfile(user_id=1)
    for name in (
        "account_risk_per_trade_pct", "daily_loss_limit_pct", "max_open_positions",
        "max_position_pct", "max_sector_exposure_pct", "max_correlated_exposure_pct",
        "max_daily_new_exposure_pct", "max_spread_bps", "max_quote_age_seconds",
        "min_average_dollar_volume", "earnings_blackout_days", "allow_overnight",
        "allowed_strategies",
    ):
        assert hasattr(rp, name)
    # Konservative Defaults (RISK-002)
    assert rp.account_risk_per_trade_pct == 0.25
    assert rp.daily_loss_limit_pct == 1.00
    assert rp.max_open_positions == 5


def test_trade_intent_has_exact_plan_fields():
    ti = domain.TradeIntent(
        user_id=1, signal_id=2, requested_action="accept",
        accepted_exit_policy="stop_take_profit", source_channel="telegram",
        created_at="2026-07-11T00:00:00Z", idempotency_key="abc-123",
    )
    assert ti.user_id == 1 and ti.signal_id == 2
    assert ti.source_channel == "telegram"
    assert ti.idempotency_key == "abc-123"
    assert ti.mode is domain.Mode.PAPER


def test_all_res002_entities_carry_mode():
    signal = domain.Signal(
        id=None, strategy_version_id=1, ticker="AAPL", direction="long",
        mode=domain.Mode.SHADOW,
    )
    intent = domain.TradeIntent(
        user_id=1, signal_id=2, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="web",
        created_at="2026-07-13T00:00:00Z", idempotency_key="intent-1",
        mode=domain.Mode.SHADOW,
    )
    order = domain.Order(
        id=None, trade_intent_id=1, user_id=1, ticker="AAPL", side="buy",
        mode=domain.Mode.SHADOW,
    )
    fill = domain.Fill(
        id=None, order_id=1, qty=1.0, price=100.0,
        filled_at="2026-07-13T00:01:00Z", mode=domain.Mode.SHADOW,
    )
    position = domain.Position(
        id=None, user_id=1, ticker="AAPL", strategy_version_id=1,
        mode=domain.Mode.SHADOW,
    )
    snapshot = domain.PerformanceSnapshot(
        id=None, strategy_version_id=1, mode=domain.Mode.SHADOW,
        captured_at="2026-07-13T00:02:00Z",
    )

    assert all(entity.mode is domain.Mode.SHADOW for entity in (
        signal, intent, order, fill, position, snapshot,
    ))


def test_new_mode_fields_are_required_and_legacy_paths_default_to_paper():
    for entity_type in (
        domain.Signal, domain.Fill, domain.Position, domain.PerformanceSnapshot,
    ):
        assert inspect.signature(entity_type).parameters["mode"].default is inspect.Parameter.empty

    for entity_type in (domain.TradeIntent, domain.Order):
        assert inspect.signature(entity_type).parameters["mode"].default is domain.Mode.PAPER


def test_audit_event_has_exact_plan_fields():
    ev = domain.AuditEvent(
        event_id="evt-1", timestamp="2026-07-11T00:00:00Z", user_id=1, actor="user:1",
        entity_type="order", entity_id="42", action="submit",
        old_state="created", new_state="submitted", trace_id="trace-1",
        source_channel="web", metadata={"ip": "1.2.3.4"},
    )
    assert ev.old_state == "created" and ev.new_state == "submitted"
    assert ev.metadata == {"ip": "1.2.3.4"}


def test_kill_switch_global_has_no_user_id():
    ks = domain.KillSwitch(
        id=1, scope="global", active=True, reason="incident",
        activated_by="ops", activated_at="2026-07-11T00:00:00Z",
    )
    assert ks.user_id is None


def test_signal_and_order_and_position_default_to_initial_status():
    signal = domain.Signal(id=None, strategy_version_id=1, ticker="AAPL",
                            direction="long", mode=domain.Mode.PAPER)
    order = domain.Order(id=None, trade_intent_id=1, user_id=1, ticker="AAPL", side="buy")
    position = domain.Position(id=None, user_id=1, ticker="AAPL",
                                strategy_version_id=1, mode=domain.Mode.PAPER)

    assert signal.status is domain.SignalStatus.GENERATED
    assert order.status is domain.OrderStatus.CREATED
    assert position.status is domain.PositionStatus.PENDING_OPEN


def test_signal_has_all_data_provenance_fields_from_plan_section_10_3():
    """DATA-005 „Datenherkunft je Berechnung speichern": Provider, Feed, Abrufzeit,
    Exchange-Zeit, Datenversion, Qualitätsstatus."""
    signal = domain.Signal(
        id=None, strategy_version_id=1, ticker="AAPL", direction="long", mode=domain.Mode.PAPER,
        data_provider="yfinance_research", data_feed="delayed",
        data_fetched_at="2026-07-11T15:35:00Z", data_exchange_time="2026-07-11T15:34:58Z",
        data_version="2026-07-11", data_quality_status="ok",
    )
    for name in ("data_provider", "data_feed", "data_fetched_at", "data_exchange_time",
                "data_version", "data_quality_status"):
        assert hasattr(signal, name)
    assert signal.data_provider == "yfinance_research"
    assert signal.data_quality_status == "ok"


def test_signal_data_provenance_fields_default_to_none():
    signal = domain.Signal(id=None, strategy_version_id=1, ticker="AAPL",
                           direction="long", mode=domain.Mode.PAPER)
    assert signal.data_provider is None
    assert signal.data_feed is None
    assert signal.data_fetched_at is None
    assert signal.data_exchange_time is None
    assert signal.data_version is None
    assert signal.data_quality_status is None


def test_strategy_version_is_frozen_and_immutable():
    sv = domain.StrategyVersion(id=1, strategy_id=1, version=1, release_status="live")
    try:
        sv.release_status = "archived"
        assert False, "StrategyVersion darf nicht mutierbar sein (unveränderlich veröffentlicht)"
    except AttributeError:
        pass
