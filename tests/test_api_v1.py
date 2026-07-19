"""Tests für API v1 (W7): Trace-ID, RBAC, Idempotenz — seit der Integration gegen echte DB."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockbot import config
from stockbot.core import db
from stockbot.web.api_v1 import build_api_v1_app

ADMIN_ID = 999001
USER_ID = 555002


@pytest.fixture(autouse=True)
def _admin_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_CHAT_ID", ADMIN_ID, raising=False)
    # Seit der W7-Integration laufen /signals und /kill-switch gegen die echte DB-Schicht.
    monkeypatch.setattr(db, "DB_FILE", Path(tmp_path) / "api_v1.db")
    db.init_db()
    from stockbot.web import webapp
    webapp.kill_switch_service.reload()


def _client(user_id):
    app = build_api_v1_app(user_provider=lambda: {"user_id": user_id, "username": "t"})
    return TestClient(app)


def test_health_returns_trace_id_in_body_and_header():
    client = _client(USER_ID)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["version"] == "v1"
    assert body["trace_id"]
    assert resp.headers["X-Trace-Id"] == body["trace_id"]      # Trace-ID in JEDER Antwort


def test_incoming_trace_id_is_propagated():
    client = _client(USER_ID)
    resp = client.get("/api/v1/health", headers={"X-Trace-Id": "abc123"})
    assert resp.headers["X-Trace-Id"] == "abc123"
    assert resp.json()["trace_id"] == "abc123"


def test_rbac_user_may_read_signals():
    resp = _client(USER_ID).get("/api/v1/signals")
    assert resp.status_code == 200


def test_rbac_regular_user_cannot_manage_kill_switch():
    resp = _client(USER_ID).post("/api/v1/kill-switch", json={"active": True},
                                 headers={"Idempotency-Key": "k1"})
    assert resp.status_code == 403


def test_admin_may_manage_kill_switch():
    resp = _client(ADMIN_ID).post("/api/v1/kill-switch", json={"active": True},
                                  headers={"Idempotency-Key": "k1"})
    assert resp.status_code == 200
    assert resp.json()["active"] is True


def test_mutating_action_requires_idempotency_key():
    resp = _client(ADMIN_ID).post("/api/v1/kill-switch", json={"active": True})
    assert resp.status_code == 400


def test_same_idempotency_key_runs_exactly_once():
    client = _client(ADMIN_ID)
    first = client.post("/api/v1/kill-switch", json={"active": True},
                        headers={"Idempotency-Key": "same"})
    # zweiter Request, GLEICHER Key, ANDERER Body → muss das ERSTE Ergebnis zurückgeben (kein Re-Run)
    second = client.post("/api/v1/kill-switch", json={"active": False},
                         headers={"Idempotency-Key": "same"})
    assert first.json()["active"] is True and first.json()["replayed"] is False
    assert second.json()["active"] is True and second.json()["replayed"] is True


# ── W7-Integration: echte Quellen + Einhängung in die Dashboard-App ──────────

def test_signals_returns_users_pending_trades():
    db.get_or_create_user(USER_ID, "t")
    db.add_pending(USER_ID, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                             "strength": 61, "raw_score": 61, "strategy": "standard"},
                   message_id=1)
    resp = _client(USER_ID).get("/api/v1/signals")
    assert resp.status_code == 200
    signals = resp.json()["signals"]
    assert [s["ticker"] for s in signals] == ["AAPL"]
    assert signals[0]["strategy"] == "standard"
    assert signals[0]["raw_score"] == 61.0


def test_kill_switch_toggle_hits_real_service():
    from stockbot.web import webapp
    client = _client(ADMIN_ID)
    client.post("/api/v1/kill-switch", json={"active": True, "reason": "test"},
                headers={"Idempotency-Key": "on"})
    status = webapp.kill_switch_service.global_status
    assert status is not None and status.active is True
    assert f"api:{ADMIN_ID}" == status.activated_by

    resp = client.post("/api/v1/kill-switch", json={"active": False},
                       headers={"Idempotency-Key": "off"})
    assert resp.json()["active"] is False
    status = webapp.kill_switch_service.global_status
    assert status is None or status.active is False


def test_api_v1_is_mounted_in_dashboard_app():
    from stockbot.web.dashboard import app
    resp = TestClient(app).get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.headers["X-Trace-Id"]                     # Middleware aktiv in der echten App
    # Session-Auth der Web-App gilt: ohne Login ist die API zu (401), nicht offen
    assert TestClient(app).get("/api/v1/signals").status_code == 401
