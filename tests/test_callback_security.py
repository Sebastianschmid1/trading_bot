"""Tests für sichere Telegram-Callbacks (W7)."""

import re
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
    """Das Token trägt weder Aktion noch Payload — es ist reines Zufallsmaterial.

    Der Payload-Anteil ließ sich nicht per Substring prüfen: eine zweistellige ID wie „99"
    steht in rund 0,8 % aller `secrets.token_urlsafe(24)`-Werte zufällig drin, was den Test
    sporadisch rot machte. Belegt wird deshalb die Eigenschaft selbst — gleiche Eingabe,
    jedes Mal ein anderes, formatreines Token, also kein deterministischer Abdruck.
    """
    tokens = {cbs.issue(1, "close_position", {"trade_id": 99}) for _ in range(20)}

    assert len(tokens) == 20
    for token in tokens:
        assert re.fullmatch(r"[A-Za-z0-9_-]{32,}", token)        # URL-safe, keine Struktur
        assert "close_position" not in token                     # kein Klartext in der callback_data


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
