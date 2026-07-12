"""
Tests für das Tagesverlustlimit (RISK-004, stockbot/core/daily_loss_limit.py).
"""

from stockbot.core import daily_loss_limit as dll
from stockbot.core.domain import RiskProfile

DEFAULT_PROFILE = RiskProfile(user_id=1)   # daily_loss_limit_pct=1.00 (konservativer Default)


def test_ok_when_no_realized_loss_yet():
    status = dll.check_daily_loss_limit(
        realized_pnl_today=0.0, account_value=10_000.0, risk_profile=DEFAULT_PROFILE)
    assert status.ok is True
    assert status.limit_amount == 100.0   # 1% von 10000


def test_ok_when_realized_gain():
    status = dll.check_daily_loss_limit(
        realized_pnl_today=50.0, account_value=10_000.0, risk_profile=DEFAULT_PROFILE)
    assert status.ok is True


def test_ok_when_loss_below_limit():
    status = dll.check_daily_loss_limit(
        realized_pnl_today=-99.0, account_value=10_000.0, risk_profile=DEFAULT_PROFILE)
    assert status.ok is True


def test_blocked_when_loss_reaches_limit_exactly():
    status = dll.check_daily_loss_limit(
        realized_pnl_today=-100.0, account_value=10_000.0, risk_profile=DEFAULT_PROFILE)
    assert status.ok is False
    assert status.code == "daily_loss_limit_reached"


def test_blocked_when_loss_exceeds_limit():
    status = dll.check_daily_loss_limit(
        realized_pnl_today=-250.0, account_value=10_000.0, risk_profile=DEFAULT_PROFILE)
    assert status.ok is False
    assert status.code == "daily_loss_limit_reached"
    assert "Tagesverlustlimit" in status.reason


def test_rejects_non_positive_account_value():
    status = dll.check_daily_loss_limit(
        realized_pnl_today=-10.0, account_value=0.0, risk_profile=DEFAULT_PROFILE)
    assert status.ok is False and status.code == "invalid_account_value"


def test_respects_custom_daily_loss_limit_pct():
    loose_profile = RiskProfile(user_id=1, daily_loss_limit_pct=5.0)
    status = dll.check_daily_loss_limit(
        realized_pnl_today=-200.0, account_value=10_000.0, risk_profile=loose_profile)
    assert status.ok is True    # -200 > -500 (5% Limit)
