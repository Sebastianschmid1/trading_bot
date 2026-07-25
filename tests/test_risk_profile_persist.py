"""Persistiertes RiskProfile pro Nutzer + realisierter Tages-P&L (RISK-004-Wiring).

Belegt: ohne gespeichertes Profil liefert der Loader ``None`` (Aufrufer fällt auf das
permissive Default zurück → unverändertes Verhalten); ein gespeichertes Profil roundtrippt
verlustfrei; ``get_realized_pnl_today`` summiert nur die HEUTE (UTC-Tag) geschlossenen Trades.
"""

import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

from stockbot.core import db
from stockbot.core.domain import RiskProfile


@pytest.fixture(autouse=True)
def fresh_db():
    d = tempfile.mkdtemp(prefix="riskprofile_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()
    yield


def _insert_closed_trade(user_id: int, ticker: str, trade_date: str, pnl_eur):
    """Fügt direkt über den Seam einen geschlossenen Trade mit gegebenem pnl_eur ein."""
    with db._database().transaction() as transaction:
        transaction.execute(
            """INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json,
                                    status, pnl_eur, created_at)
               VALUES (:user_id, :trade_date, :ticker, 'long', '{}', 'closed', :pnl_eur,
                       :created_at)""",
            {"user_id": user_id, "trade_date": trade_date, "ticker": ticker,
             "pnl_eur": pnl_eur, "created_at": db._utc_timestamp()},
        )


# -- Persistenz ----------------------------------------------------------------

def test_get_risk_profile_returns_none_when_unset():
    # Ohne gespeichertes Profil: None → der Aufrufer nutzt das permissive Default-RiskProfile.
    assert db.get_risk_profile(42) is None


def test_save_and_load_roundtrips_all_fields():
    profile = RiskProfile(
        user_id=42, account_risk_per_trade_pct=0.5, daily_loss_limit_pct=2.5,
        max_open_positions=3, max_position_pct=20.0, max_sector_exposure_pct=40.0,
        max_correlated_exposure_pct=30.0, max_daily_new_exposure_pct=15.0,
        max_spread_bps=25.0, max_quote_age_seconds=30, min_average_dollar_volume=1_000_000.0,
        earnings_blackout_days=2, allow_overnight=False,
        allowed_strategies=("standard", "bb_revert"),
    )
    db.save_risk_profile(profile)

    assert db.get_risk_profile(42) == profile


def test_save_is_upsert():
    db.save_risk_profile(RiskProfile(user_id=42, daily_loss_limit_pct=1.0))
    db.save_risk_profile(RiskProfile(user_id=42, daily_loss_limit_pct=3.0, max_open_positions=9))

    loaded = db.get_risk_profile(42)
    assert loaded.daily_loss_limit_pct == 3.0
    assert loaded.max_open_positions == 9


def test_stored_profile_is_per_user():
    db.save_risk_profile(RiskProfile(user_id=1, daily_loss_limit_pct=1.0))
    assert db.get_risk_profile(1) is not None
    assert db.get_risk_profile(2) is None


# -- Realisierter Tages-P&L ----------------------------------------------------

def test_realized_pnl_today_is_zero_without_trades():
    assert db.get_realized_pnl_today(42) == 0.0


def test_realized_pnl_today_sums_todays_closed_trades():
    today = db.today_utc()
    _insert_closed_trade(42, "AAPL", today, 120.0)
    _insert_closed_trade(42, "MSFT", today, -50.0)

    assert db.get_realized_pnl_today(42) == pytest.approx(70.0)


def test_realized_pnl_today_ignores_other_days_and_users():
    today = db.today_utc()
    yesterday = str(db.today_utc_date() - timedelta(days=1))
    _insert_closed_trade(42, "AAPL", today, 120.0)
    _insert_closed_trade(42, "OLD", yesterday, -1000.0)   # anderer UTC-Tag
    _insert_closed_trade(99, "NVDA", today, -777.0)        # anderer Nutzer

    assert db.get_realized_pnl_today(42) == pytest.approx(120.0)


def test_realized_pnl_today_uses_utc_day_not_local(monkeypatch):
    # TZ-unabhängig: die Tagesgrenze kommt aus today_utc(), nicht aus der Server-Lokalzeit.
    forced_day = "2026-03-15"
    monkeypatch.setattr(db, "today_utc", lambda: forced_day)
    _insert_closed_trade(42, "AAPL", forced_day, 42.0)
    _insert_closed_trade(42, "MSFT", "2026-03-14", -500.0)

    assert db.get_realized_pnl_today(42) == pytest.approx(42.0)
