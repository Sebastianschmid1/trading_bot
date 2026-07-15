from datetime import datetime, timedelta, timezone

import pytest

from stockbot import config
from stockbot.core.domain import RiskProfile
from stockbot.core.risk import pretrade_check


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
PROFILE = RiskProfile(
    user_id=1, account_risk_per_trade_pct=1.0, max_open_positions=5,
    max_position_pct=10.0,
)


@pytest.mark.parametrize(
    ("inputs", "expected_code"),
    [
        pytest.param(
            {
                "now": NOW, "signal_expires_at": NOW + timedelta(minutes=5),
                "entry_price": 100.0, "stop_price": 95.0,
                "account_value": 10_000.0,
                "risk_profile": RiskProfile(
                    user_id=1, account_risk_per_trade_pct=1.0,
                    max_position_pct=100.0,
                ),
            },
            "",
            id="clean_with_sizing",
        ),
        pytest.param(
            {"now": NOW, "open_position_count": 5, "risk_profile": PROFILE},
            "max_positions_reached",
            id="max_positions_reached",
        ),
        pytest.param(
            {"now": NOW, "leverage": config.MAX_LEVERAGE + 1.0},
            "leverage_blocked",
            id="leverage_blocked",
        ),
        pytest.param(
            {"now": NOW, "signal_expires_at": NOW - timedelta(seconds=1)},
            "signal_expired",
            id="signal_expired",
        ),
        pytest.param(
            {
                "now": NOW, "candidate_notional": 2_000.0,
                "account_value": 10_000.0, "risk_profile": PROFILE,
            },
            "single_position_exposure",
            id="exposure_rejected",
        ),
        pytest.param(
            {
                "now": NOW, "entry_price": 100.0, "stop_price": 100.0,
                "account_value": 10_000.0, "risk_profile": PROFILE,
            },
            "invalid_stop_distance",
            id="sizing_rejected",
        ),
    ],
)
def test_pretrade_check_is_deterministic(inputs, expected_code):
    decisions = [pretrade_check(**inputs) for _ in range(5)]

    assert decisions[0].code == expected_code
    assert all(decision == decisions[0] for decision in decisions[1:])
    assert all(
        (decision.ok, decision.code, decision.reason, decision.sizing)
        == (decisions[0].ok, decisions[0].code, decisions[0].reason, decisions[0].sizing)
        for decision in decisions[1:]
    )


def test_absolute_rejection_order_is_stable(monkeypatch):
    monkeypatch.setattr(config, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(config, "ALLOW_OPTIONS", False)
    violations = {
        "now": NOW,
        "is_live_account": True,
        "leverage": config.MAX_LEVERAGE + 1.0,
        "is_option": True,
    }

    decisions = [pretrade_check(**violations) for _ in range(5)]

    assert [decision.code for decision in decisions] == ["live_blocked"] * 5
