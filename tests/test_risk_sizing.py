"""
Tests für das risikobasierte Position-Sizing (RISK-002, stockbot/core/risk_sizing.py,
Plan.md §11.2).
"""

from stockbot.core import risk_sizing
from stockbot.core.domain import RiskProfile

DEFAULT_PROFILE = RiskProfile(user_id=1)   # konservative Defaults: 0.25%/Trade, max_position_pct=100


def test_size_position_uses_risk_formula():
    result = risk_sizing.size_position(
        account_value=10_000.0, entry_price=100.0, stop_price=98.0, risk_profile=DEFAULT_PROFILE)
    assert result.ok is True
    assert result.risk_amount == 25.0            # 10000 * 0.25 / 100
    assert result.qty == 12.5                     # 25 / (100 - 98)
    assert result.notional == 1250.0
    assert result.code == ""


def test_size_position_works_with_stop_above_entry_for_shorts():
    # abs(entry - stop) — Richtung des Stops ist dem Sizing egal (long oder short)
    result = risk_sizing.size_position(
        account_value=10_000.0, entry_price=100.0, stop_price=102.0, risk_profile=DEFAULT_PROFILE)
    assert result.ok is True
    assert result.qty == 12.5


def test_size_position_caps_at_max_position_pct():
    tight_profile = RiskProfile(user_id=1, max_position_pct=1.0)   # max 1% des Kontos je Position
    result = risk_sizing.size_position(
        account_value=10_000.0, entry_price=100.0, stop_price=98.0, risk_profile=tight_profile)
    assert result.ok is True
    assert result.notional == 100.0                # 10000 * 1% Deckel
    assert result.qty == 1.0
    assert result.code == "capped_by_max_position_pct"
    assert "gedeckelt" in result.reason


def test_size_position_rejects_non_positive_account_value():
    result = risk_sizing.size_position(
        account_value=0.0, entry_price=100.0, stop_price=98.0, risk_profile=DEFAULT_PROFILE)
    assert result.ok is False and result.code == "invalid_account_value"


def test_size_position_rejects_non_positive_entry_price():
    result = risk_sizing.size_position(
        account_value=10_000.0, entry_price=0.0, stop_price=98.0, risk_profile=DEFAULT_PROFILE)
    assert result.ok is False and result.code == "invalid_entry_price"


def test_size_position_rejects_zero_stop_distance():
    result = risk_sizing.size_position(
        account_value=10_000.0, entry_price=100.0, stop_price=100.0, risk_profile=DEFAULT_PROFILE)
    assert result.ok is False and result.code == "invalid_stop_distance"


def test_size_position_rejects_zero_quantity_from_extremely_tight_cap():
    tight_profile = RiskProfile(user_id=1, max_position_pct=0.0)
    result = risk_sizing.size_position(
        account_value=10_000.0, entry_price=100.0, stop_price=98.0, risk_profile=tight_profile)
    assert result.ok is False and result.code == "zero_quantity"
