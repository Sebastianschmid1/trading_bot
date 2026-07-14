"""Tests für den reinen Portfolio-Allocator (STRAT-006)."""

from types import SimpleNamespace

from stockbot.core.allocator import AllocationCandidate, AllocationInput, allocate
from stockbot.core.domain import (
    Mode,
    Order,
    OrderStatus,
    Position,
    PositionStatus,
    RiskProfile,
    SignalCandidate,
)


ACCOUNT = 10_000.0


def _signal(ticker: str, strategy_version_id: int = 1, raw_score: float = 50.0):
    return SignalCandidate(
        id=None,
        strategy_version_id=strategy_version_id,
        ticker=ticker,
        direction="long",
        raw_score=raw_score,
        mode=Mode.PAPER,
    )


def _candidate(ticker: str, strategy: str = "alpha", **overrides):
    fields = dict(
        candidate=_signal(ticker),
        strategy_key=strategy,
        entry_price=100.0,
        stop_price=99.0,
    )
    fields.update(overrides)
    return AllocationCandidate(**fields)


def _profile(**overrides):
    fields = dict(
        user_id=1,
        account_risk_per_trade_pct=0.25,
        daily_loss_limit_pct=10.0,
        max_open_positions=10,
        max_position_pct=100.0,
        max_sector_exposure_pct=100.0,
        max_correlated_exposure_pct=100.0,
        max_daily_new_exposure_pct=100.0,
    )
    fields.update(overrides)
    return RiskProfile(**fields)


def _inputs(candidates, **overrides):
    fields = dict(
        candidates=tuple(candidates),
        positions=(),
        open_orders=(),
        risk_profile=_profile(),
        account_value=ACCOUNT,
    )
    fields.update(overrides)
    return AllocationInput(**fields)


def test_allocation_is_deterministic_and_round_robins_by_strategy_priority():
    inputs = _inputs(
        (
            _candidate("A1", "alpha", candidate=_signal("A1", raw_score=1)),
            _candidate("B1", "beta", candidate=_signal("B1", raw_score=999)),
            _candidate("A2", "alpha", candidate=_signal("A2", raw_score=2)),
            _candidate("B2", "beta", candidate=_signal("B2", raw_score=998)),
        ),
        strategy_priorities=("beta", "alpha"),
    )

    first = allocate(inputs)
    second = allocate(inputs)

    assert first == second
    assert [c.ticker for c in first.allocation_order] == ["B1", "A1", "B2", "A2"]
    assert [item.candidate.ticker for item in first.selected] == ["B1", "A1", "B2", "A2"]


def test_duplicate_ticker_is_rejected_after_first_allocation():
    result = allocate(_inputs((_candidate("AAPL", "alpha"), _candidate("AAPL", "beta"))))

    assert [item.candidate.ticker for item in result.selected] == ["AAPL"]
    assert len(result.rejected) == 1
    assert result.rejected[0].candidate.ticker == "AAPL"
    assert result.rejected[0].code == "duplicate_ticker"


def test_max_positions_counts_positions_open_orders_and_allocations():
    position = Position(
        id=1,
        user_id=1,
        ticker="HELD",
        strategy_version_id=1,
        mode=Mode.PAPER,
        status=PositionStatus.OPEN,
    )
    order = Order(
        id=1,
        trade_intent_id=1,
        user_id=1,
        ticker="PENDING",
        side="buy",
        status=OrderStatus.SUBMITTED,
    )
    result = allocate(_inputs(
        (_candidate("FIRST"), _candidate("SECOND")),
        positions=(position,),
        open_orders=(order,),
        risk_profile=_profile(max_open_positions=3),
    ))

    assert [item.candidate.ticker for item in result.selected] == ["FIRST"]
    assert [(item.candidate.ticker, item.code) for item in result.rejected] == [
        ("SECOND", "max_positions_reached")
    ]


def test_exposure_cap_is_cumulative_within_allocation_run():
    # Bei 1% Risiko und Stop-Abstand 4 sind beide Notionals 2.500. Der erste Kandidat füllt
    # damit den 25%-Sektordeckel vollständig; der zweite muss gegen den aktualisierten Bestand
    # geprüft werden.
    profile = _profile(account_risk_per_trade_pct=1.0, max_sector_exposure_pct=25.0)
    result = allocate(_inputs(
        (
            _candidate("AAPL", sector="technology", stop_price=96.0),
            _candidate("MSFT", sector="technology", stop_price=96.0),
        ),
        risk_profile=profile,
    ))

    assert [item.candidate.ticker for item in result.selected] == ["AAPL"]
    assert result.rejected[0].code == "exposure_cap"
    assert result.rejected[0].detail_code == "sector_exposure"


def test_rejected_risk_decision_reason_and_code_are_passed_through():
    # Gleiche öffentliche Felder wie ``risk.RiskDecision``; der Core-Unit-Test importiert
    # absichtlich nicht das konfigurationsladende risk-Modul.
    decision = SimpleNamespace(
        ok=False, reason="Markt ist geschlossen.", code="market_closed", sizing=None)
    result = allocate(_inputs((_candidate("AAPL", risk_decision=decision),)))

    assert result.selected == ()
    assert result.rejected[0].reason == "Markt ist geschlossen."
    assert result.rejected[0].code == "market_closed"


def test_total_risk_budget_exhaustion_rejects_later_candidate():
    # Jeder Trade reserviert 25; der explizite Deckel von 0,5% entspricht 50.
    result = allocate(_inputs(
        (_candidate("ONE"), _candidate("TWO"), _candidate("THREE")),
        total_risk_budget_pct=0.5,
    ))

    assert [item.candidate.ticker for item in result.selected] == ["ONE", "TWO"]
    assert [(item.candidate.ticker, item.code) for item in result.rejected] == [
        ("THREE", "budget_exhausted")
    ]


def test_realized_daily_loss_reduces_available_risk_budget():
    # 1% Tageslimit = 100; nach 80 Verlust bleiben nur 20 und der 25-Risikotrade passt nicht.
    result = allocate(_inputs(
        (_candidate("ONE"),),
        risk_profile=_profile(daily_loss_limit_pct=1.0),
        realized_pnl_today=-80.0,
    ))

    assert result.selected == ()
    assert result.rejected[0].code == "budget_exhausted"


def test_reserved_risk_total_equals_sum_of_individual_reservations():
    result = allocate(_inputs((_candidate("ONE"), _candidate("TWO"))))

    assert result.reserved_risk_total == sum(
        item.reserved_risk_budget for item in result.selected)
    assert result.reserved_risk_total == 50.0


def test_expected_cost_can_push_value_below_minimum():
    result = allocate(_inputs(
        (_candidate("COSTLY", expected_value=12.0, expected_cost=3.0),),
        minimum_net_expected_value=10.0,
    ))

    assert result.selected == ()
    assert result.rejected[0].code == "expected_value_below_minimum"
