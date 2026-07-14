"""Lebenszyklus-Nachweis für die Signaltreue aus Plan §14.5."""

from pathlib import Path

from stockbot.core import db


CHAT = 6_933_293_791


def test_all_signal_outcomes_remain_persisted_and_survive_user_reset(tmp_path: Path):
    db.DB_FILE = tmp_path / "signal_retention.db"
    db.init_db()
    db.get_or_create_user(CHAT, "retention-test")

    # Veröffentlicht: bleibt pending.
    db.add_pending(CHAT, {"ticker": "PUBLISHED", "direction": "long"}, 1)

    # Vom Nutzer abgelehnt.
    db.add_pending(CHAT, {"ticker": "REJECTED", "direction": "long"}, 2)
    assert db.reject_trade(CHAT, "REJECTED")

    # Zeitfenster abgelaufen.
    db.add_pending(CHAT, {"ticker": "EXPIRED", "direction": "long"}, 3)
    assert db.expire_trade(CHAT, "EXPIRED")

    # Pre-Trade-Risk-Ablehnung; der Ablehnungscode bleibt am Trade erhalten.
    db.add_pending(CHAT, {"ticker": "RISK", "direction": "long", "price": 100.0}, 4)
    assert db.activate_trade(CHAT, "RISK", status="broker_pending")
    assert db.mark_broker_failed(CHAT, "RISK", broker_status="leverage_blocked")

    # Nicht an den Broker übermittelt/gefüllt.
    db.add_pending(CHAT, {"ticker": "UNFILLED", "direction": "long", "price": 100.0}, 5)
    assert db.activate_trade(CHAT, "UNFILLED", status="broker_pending")
    assert db.mark_broker_failed(CHAT, "UNFILLED", broker_status="not_submitted")

    expected = {
        "PUBLISHED": ("pending", None),
        "REJECTED": ("rejected", None),
        "EXPIRED": ("expired", None),
        "RISK": ("broker_failed", "leverage_blocked"),
        "UNFILLED": ("broker_failed", "not_submitted"),
    }
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT ticker, status, broker_status FROM trades WHERE user_id = ?", (CHAT,)
        ).fetchall()
    assert {row["ticker"]: (row["status"], row["broker_status"]) for row in rows} == expected

    assert db.reset_user_trades(CHAT) == 5
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM trades WHERE user_id = ?", (CHAT,)
        ).fetchone()[0] == 0
        archived = conn.execute(
            "SELECT ticker, status, broker_status, archive_reason, archived_at "
            "FROM trades_archive WHERE user_id = ?", (CHAT,)
        ).fetchall()
    assert {row["ticker"]: (row["status"], row["broker_status"]) for row in archived} == expected
    assert all(row["archive_reason"] == "user_reset" and row["archived_at"] for row in archived)
