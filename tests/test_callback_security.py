"""Tests für sichere Telegram-Callbacks (W7)."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from stockbot.core import db
from stockbot.tgbot import callback_security as cbs


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", Path(tmp_path) / "cb.db")
    db.init_db()
    return db


def test_issue_and_resolve_returns_action_and_payload(fresh_db):
    token = cbs.issue(42, "accept_signal", {"signal_id": 7})
    action, payload = cbs.resolve(token, 42)
    assert action == "accept_signal"
    assert payload == {"signal_id": 7}


def test_token_is_opaque(fresh_db):
    token = cbs.issue(1, "close_position", {"trade_id": 99})
    assert "close_position" not in token and "99" not in token   # kein Klartext in der callback_data


def test_one_time_use_blocks_replay(fresh_db):
    token = cbs.issue(5, "accept_signal", {"x": 1})
    cbs.resolve(token, 5)
    with pytest.raises(cbs.CallbackSecurityError) as e:
        cbs.resolve(token, 5)                    # Doppel-Callback / Replay
    assert e.value.reason == "used"


def test_user_binding_enforced(fresh_db):
    token = cbs.issue(5, "accept_signal", {})
    with pytest.raises(cbs.CallbackSecurityError) as e:
        cbs.resolve(token, 6)                    # anderer Nutzer
    assert e.value.reason == "wrong_user"
    # nach abgewiesenem Fremdzugriff bleibt es für den echten Nutzer gültig
    action, _ = cbs.resolve(token, 5)
    assert action == "accept_signal"


def test_expired_token_rejected(fresh_db):
    token = cbs.issue(5, "accept_signal", {}, ttl_seconds=-1)   # sofort abgelaufen
    with pytest.raises(cbs.CallbackSecurityError) as e:
        cbs.resolve(token, 5)
    assert e.value.reason == "expired"


def test_unknown_token_rejected(fresh_db):
    with pytest.raises(cbs.CallbackSecurityError) as e:
        cbs.resolve("nicht-existent", 5)
    assert e.value.reason == "unknown"


def test_purge_expired(fresh_db):
    cbs.issue(5, "a", {}, ttl_seconds=-1)
    valid = cbs.issue(5, "b", {}, ttl_seconds=300)
    db.purge_expired_callback_tokens(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    # das gültige Token übersteht die Bereinigung
    action, _ = cbs.resolve(valid, 5)
    assert action == "b"
