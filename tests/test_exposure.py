"""
Tests für die Exposure-Limits (RISK-005, stockbot/core/exposure.py).
"""

from stockbot.core import exposure as exp
from stockbot.core.domain import RiskProfile

ACCOUNT = 10_000.0


def _profile(**overrides):
    fields = dict(user_id=1, max_position_pct=20.0, max_sector_exposure_pct=40.0,
                 max_correlated_exposure_pct=40.0, max_daily_new_exposure_pct=30.0)
    fields.update(overrides)
    return RiskProfile(**fields)


# ── Einzel-Exposure ───────────────────────────────────────────────────────────

def test_single_position_ok_within_limit():
    d = exp.check_single_position_exposure(
        candidate_notional=1500.0, account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is True


def test_single_position_blocked_over_limit():
    d = exp.check_single_position_exposure(
        candidate_notional=2500.0, account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is False and d.code == "single_position_exposure"


# ── Sektor-Exposure ───────────────────────────────────────────────────────────

def test_sector_exposure_skipped_without_sector():
    d = exp.check_sector_exposure(
        candidate_notional=100_000.0, candidate_sector=None, positions=(),
        account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is True


def test_sector_exposure_ok_within_limit():
    positions = (exp.ExposurePosition(ticker="AAPL", notional=1000.0, sector="tech"),)
    d = exp.check_sector_exposure(
        candidate_notional=2000.0, candidate_sector="tech", positions=positions,
        account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is True   # 1000 + 2000 = 3000 <= 4000 (40%)


def test_sector_exposure_blocked_over_limit():
    positions = (exp.ExposurePosition(ticker="AAPL", notional=3000.0, sector="tech"),)
    d = exp.check_sector_exposure(
        candidate_notional=2000.0, candidate_sector="tech", positions=positions,
        account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is False and d.code == "sector_exposure"


def test_sector_exposure_ignores_other_sectors():
    positions = (exp.ExposurePosition(ticker="XOM", notional=3900.0, sector="energy"),)
    d = exp.check_sector_exposure(
        candidate_notional=2000.0, candidate_sector="tech", positions=positions,
        account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is True


# ── Korrelierte Exposure ──────────────────────────────────────────────────────

def test_correlated_exposure_skipped_without_group():
    d = exp.check_correlated_exposure(
        candidate_notional=100_000.0, candidate_correlation_group=None, positions=(),
        account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is True


def test_correlated_exposure_blocked_over_limit():
    positions = (exp.ExposurePosition(ticker="NVDA", notional=3000.0, correlation_group="ai_chips"),)
    d = exp.check_correlated_exposure(
        candidate_notional=2000.0, candidate_correlation_group="ai_chips", positions=positions,
        account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is False and d.code == "correlated_exposure"


# ── Täglich neue Exposure ─────────────────────────────────────────────────────

def test_daily_new_exposure_ignores_older_positions():
    positions = (exp.ExposurePosition(ticker="AAPL", notional=5000.0, opened_today=False),)
    d = exp.check_daily_new_exposure(
        candidate_notional=1000.0, positions=positions, account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is True   # nur heutige Positionen zählen


def test_daily_new_exposure_blocked_over_limit():
    positions = (exp.ExposurePosition(ticker="AAPL", notional=2500.0, opened_today=True),)
    d = exp.check_daily_new_exposure(
        candidate_notional=1000.0, positions=positions, account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is False and d.code == "daily_new_exposure"   # 2500+1000=3500 > 3000 (30%)


# ── evaluate_exposure (Bündelung) ─────────────────────────────────────────────

def test_evaluate_exposure_ok_with_no_positions():
    d = exp.evaluate_exposure(candidate_notional=1000.0, account_value=ACCOUNT, risk_profile=_profile())
    assert d.ok is True


def test_evaluate_exposure_blocks_on_single_position_first():
    positions = (exp.ExposurePosition(ticker="AAPL", notional=3000.0, sector="tech", opened_today=True),)
    d = exp.evaluate_exposure(
        candidate_notional=2500.0, account_value=ACCOUNT, risk_profile=_profile(),
        candidate_sector="tech", positions=positions)
    # 2500 allein schon > 20% Einzel-Limit (2000) -> muss VOR dem Sektor-Check blockieren
    assert d.ok is False and d.code == "single_position_exposure"


def test_evaluate_exposure_blocks_on_sector_when_single_position_ok():
    positions = (exp.ExposurePosition(ticker="AAPL", notional=3000.0, sector="tech"),)
    d = exp.evaluate_exposure(
        candidate_notional=1500.0, account_value=ACCOUNT, risk_profile=_profile(),
        candidate_sector="tech", positions=positions)
    # 1500 <= 2000 (Einzel-OK), aber 3000+1500=4500 > 4000 (40% Sektor-Limit)
    assert d.ok is False and d.code == "sector_exposure"


def test_evaluate_exposure_rejects_non_positive_account_value():
    d = exp.evaluate_exposure(candidate_notional=100.0, account_value=0.0, risk_profile=_profile())
    assert d.ok is False and d.code == "invalid_account_value"
