"""Tests für den Alpaca-OAuth-Verbindungs-Seam (W4.4, PLAT-007)."""

from pathlib import Path

import pytest

from stockbot.core import db
from stockbot.execution import broker_oauth


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", Path(tmp_path) / "oauth.db")
    db.init_db()
    return db


class _FakeRevoker:
    def __init__(self):
        self.revoked = []

    def revoke(self, access_token):
        self.revoked.append(access_token)


def test_store_and_get_roundtrip_decrypts(fresh_db):
    broker_oauth.store_connection(user_id=1, mode="paper", access_token="tok-abc",
                                  refresh_token="ref-xyz", scopes=["trading", "data"])
    conn = broker_oauth.get_connection(1, "paper")
    assert conn["access_token"] == "tok-abc"
    assert conn["refresh_token"] == "ref-xyz"
    assert conn["scopes"] == "trading,data"
    assert conn["mode"] == "paper"


def test_tokens_are_encrypted_at_rest(fresh_db):
    broker_oauth.store_connection(user_id=2, mode="paper", access_token="secret-token",
                                  refresh_token=None)
    with db._database().transaction() as tx:
        row = tx.one("SELECT access_token FROM broker_oauth_connections WHERE user_id = 2", {})
    raw = row["access_token"]
    assert b"secret-token" not in bytes(raw)          # kein Klartext in der Spalte
    assert db.decrypt(raw) == "secret-token"          # aber entschlüsselbar


def test_paper_and_live_are_separate(fresh_db):
    broker_oauth.store_connection(user_id=3, mode="paper", access_token="paper-tok")
    broker_oauth.store_connection(user_id=3, mode="live", access_token="live-tok")
    assert broker_oauth.get_connection(3, "paper")["access_token"] == "paper-tok"
    assert broker_oauth.get_connection(3, "live")["access_token"] == "live-tok"


def test_store_replaces_existing_connection(fresh_db):
    broker_oauth.store_connection(user_id=4, mode="paper", access_token="old")
    broker_oauth.store_connection(user_id=4, mode="paper", access_token="new")
    assert broker_oauth.get_connection(4, "paper")["access_token"] == "new"
    with db._database().transaction() as tx:
        rows = tx.all("SELECT id FROM broker_oauth_connections WHERE user_id = 4", {})
    assert len(rows) == 1                              # kein Duplikat


def test_disconnect_removes_connection(fresh_db):
    broker_oauth.store_connection(user_id=5, mode="paper", access_token="t")
    broker_oauth.disconnect(5, "paper")
    assert broker_oauth.get_connection(5, "paper") is None


def test_revoke_calls_client_and_locks_out(fresh_db):
    broker_oauth.store_connection(user_id=6, mode="paper", access_token="live-token")
    revoker = _FakeRevoker()
    assert broker_oauth.revoke(6, "paper", revoke_client=revoker) is True
    assert revoker.revoked == ["live-token"]           # Broker-Revoke aufgerufen
    assert broker_oauth.get_connection(6, "paper") is None   # sofort kein Zugriff mehr
    # Token in der widerrufenen Zeile ist überschrieben (kein nutzbares Token bleibt)
    with db._database().transaction() as tx:
        row = tx.one("SELECT access_token, revoked_at FROM broker_oauth_connections "
                     "WHERE user_id = 6", {})
    assert row["revoked_at"] is not None
    assert db.decrypt(row["access_token"]) == ""


def test_revoke_without_connection_returns_false(fresh_db):
    assert broker_oauth.revoke(999, "paper", revoke_client=_FakeRevoker()) is False


def test_invalid_mode_rejected(fresh_db):
    with pytest.raises(ValueError):
        broker_oauth.store_connection(user_id=7, mode="margin", access_token="t")
    with pytest.raises(ValueError):
        broker_oauth.get_connection(7, "sandbox")
