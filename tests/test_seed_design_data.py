"""
Tests für das Design-Seed-Skript (tools/seed_design_data.py) — reproduzierbare Demo-Daten
für die visuelle Design-Abnahme der Web-App.

Lauf:  pytest tests/test_seed_design_data.py   (offline)
"""

import tempfile
from pathlib import Path

import pytest

from stockbot import config
from stockbot.core import db

import tools.seed_design_data as seed_design_data


def _fresh_target() -> Path:
    d = tempfile.mkdtemp(prefix="design_seed_test_")
    return Path(d) / "design_seed.db"


def _counts(user_id: int) -> dict:
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT status, COUNT(*) AS n FROM trades WHERE user_id = :uid GROUP BY status",
            {"uid": user_id},
        )
    return {row["status"]: row["n"] for row in rows}


def test_seed_writes_expected_states_and_dashboard_token():
    target = _fresh_target()
    result = seed_design_data.seed(target)

    assert result["db_path"] == target.resolve()
    assert result["token"]
    counts = result["status_counts"]

    # Alle im Auftrag geforderten Zustände sind vertreten.
    assert counts["active"] == 2                # offen: Gewinn + Verlust
    assert counts["closed"] == 11                # mehrere Tage Verlauf
    assert counts["pending"] == 1                # unentschiedenes Signal
    assert counts["broker_failed"] == 3           # echte Ablehngründe (spread_wide/…)
    assert counts["rejected"] == 1                # einfache manuelle Ablehnung

    with db._database().transaction() as transaction:
        broker_failed_reasons = {
            row["ticker"]: row["broker_status"]
            for row in transaction.all(
                "SELECT ticker, broker_status FROM trades "
                "WHERE user_id = :uid AND status = 'broker_failed'",
                {"uid": seed_design_data.USER_ID},
            )
        }
    assert set(broker_failed_reasons.values()) == {
        seed_design_data.REASON_SPREAD_WIDE,
        seed_design_data.REASON_MAX_POSITIONS,
        seed_design_data.REASON_QUOTE_STALE,
    }

    # Randfälle: sehr langer Ticker, sechsstelliger Betrag, negativer Extremwert, exakt 0.
    with db._database().transaction() as transaction:
        by_ticker = {
            row["ticker"]: row
            for row in transaction.all(
                "SELECT ticker, pnl_eur FROM trades WHERE user_id = :uid AND status = 'closed'",
                {"uid": seed_design_data.USER_ID},
            )
        }
    assert "SUPERCALIFRAGILISTICEXPIALIDOCIOUSAG" in by_ticker
    assert by_ticker["BRKA"]["pnl_eur"] >= 100_000
    assert by_ticker["SPY"]["pnl_eur"] <= -10_000
    assert by_ticker["IWM"]["pnl_eur"] == 0.0

    # Kill-Switch (Nutzer-Scope) ist aktiv.
    with db._database().transaction() as transaction:
        ks = transaction.one(
            "SELECT * FROM kill_switches WHERE scope = 'user' AND user_id = :uid AND active = 1",
            {"uid": seed_design_data.USER_ID},
        )
    assert ks is not None

    # Bewusst leerer Bereich: Watchlist des Demo-Nutzers bleibt leer.
    user = db.get_user(seed_design_data.USER_ID)
    assert user["watchlist"] == []

    # Kein Broker-Key für den Demo-Nutzer (Sicherheitsauflage).
    assert db.get_decrypted_credentials(seed_design_data.USER_ID) is None


def test_seed_is_idempotent_no_duplicates_on_second_run():
    target = _fresh_target()
    first = seed_design_data.seed(target)
    second = seed_design_data.seed(target)

    assert first["token"] == second["token"]                 # Login-Link bleibt stabil
    assert first["status_counts"] == second["status_counts"]  # identischer Zustand

    with db._database().transaction() as transaction:
        total = transaction.one(
            "SELECT COUNT(*) AS n FROM trades WHERE user_id = :uid",
            {"uid": seed_design_data.USER_ID},
        )["n"]
        dupes = transaction.all(
            """SELECT user_id, trade_date, ticker, COUNT(*) AS n FROM trades
               WHERE user_id = :uid GROUP BY user_id, trade_date, ticker HAVING COUNT(*) > 1""",
            {"uid": seed_design_data.USER_ID},
        )
        users_rows = transaction.one(
            "SELECT COUNT(*) AS n FROM users WHERE user_id = :uid",
            {"uid": seed_design_data.USER_ID},
        )["n"]
        kill_switch_rows = transaction.one(
            "SELECT COUNT(*) AS n FROM kill_switches WHERE user_id = :uid",
            {"uid": seed_design_data.USER_ID},
        )["n"]

    assert total == sum(first["status_counts"].values())
    assert dupes == []                # keine Dubletten je (user_id, trade_date, ticker)
    assert users_rows == 1            # kein doppelter Nutzer-Datensatz
    assert kill_switch_rows == 1      # keine akkumulierenden Kill-Switch-Zeilen


def test_seed_refuses_postgres_backend_and_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_BACKEND", "postgres")
    target = tmp_path / "must_not_exist.db"

    with pytest.raises(SystemExit):
        seed_design_data.seed(target)

    assert not target.exists()


def test_default_db_path_is_not_bot_db():
    assert seed_design_data.DEFAULT_DB_PATH.name != "bot.db"
    assert seed_design_data.DEFAULT_DB_PATH != seed_design_data.FORBIDDEN_DB_PATH


def test_seed_refuses_to_target_bot_db_path():
    with pytest.raises(SystemExit):
        seed_design_data.seed(seed_design_data.FORBIDDEN_DB_PATH)
