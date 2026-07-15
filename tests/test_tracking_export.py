"""
Tests für Status-Dauer-Tracking, Log-/Trade-Downloads (Zeitraum) und den P&L-Fix.

- trade_lifecycle.compute_durations (Dauern aus Event-Log)
- logfilter.filter_log_lines (Zeitraum/Level, Mehrzeiler-Tracebacks)
- db: Event-Instrumentierung (pending→active→closed) + Reader
- webapp: Export (kind/from/to, Dauer-Spalten), Log-Endpoint (Admin-Gate)
- evaluator.effective_leverage (P&L nicht überzeichnet beim Aktien-Fallback)

Lauf:  pytest tests/test_tracking_export.py   (offline)
"""

import tempfile
from datetime import datetime
from pathlib import Path

from starlette.testclient import TestClient

from stockbot.core import db
from stockbot.core import trade_lifecycle as tl
from stockbot.core import logfilter as lf
from stockbot.core import evaluator
from stockbot.web import dashboard as _dashboard   # zuerst: vermeidet Zirkularimport
from stockbot.web import webapp

CHAT = 4242


class _FakeYF:
    def __init__(self, price): self._p = price
    def Ticker(self, t):
        price = self._p
        class _T:
            @property
            def fast_info(self):
                class _FI: last_price = price
                return _FI()
        return _T()


def _fresh():
    d = tempfile.mkdtemp(prefix="tracktest_")
    db.DB_FILE = Path(d) / "t.db"
    db.init_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=1000.0)


def _client():
    from stockbot.web.dashboard import app
    c = TestClient(app)
    tok = db.get_or_create_dashboard_token(CHAT)
    c.get(f"/auth/token?token={tok}")
    return c


# ── compute_durations ────────────────────────────────────────────────────────

def test_compute_durations_closed_trade():
    evs = [
        {"to_status": "pending", "ts": "2026-06-22 10:00:00"},
        {"to_status": "active", "ts": "2026-06-22 10:01:00"},
        {"to_status": "closed", "ts": "2026-06-22 10:03:00"},
    ]
    d = tl.compute_durations(evs)
    assert d["pending_sec"] == 60 and d["hold_sec"] == 120
    assert d["total_lifetime_sec"] == 180 and d["is_open"] is False


def test_compute_durations_open_trade_runs_to_now():
    evs = [
        {"to_status": "pending", "ts": "2026-06-22 10:00:00"},
        {"to_status": "broker_pending", "ts": "2026-06-22 10:00:10"},
        {"to_status": "active", "ts": "2026-06-22 10:00:40"},
    ]
    d = tl.compute_durations(evs, now=datetime(2026, 6, 22, 10, 5, 40))
    assert d["time_to_fill_sec"] == 30 and d["hold_sec"] == 300 and d["is_open"] is True


def test_compute_durations_empty():
    d = tl.compute_durations([])
    assert d["total_lifetime_sec"] is None and d["by_status"] == {}


def test_parse_ts_normalises_aware_values_to_naive_utc():
    expected = datetime(2026, 7, 15, 13, 35, 14, 906789)
    assert tl.parse_ts("2026-07-15 13:35:14.906789+00") == expected
    assert tl.parse_ts("2026-07-15T13:35:14+00:00") == expected.replace(microsecond=0)
    naive = tl.parse_ts("2026-07-15 13:35:14")
    assert naive == expected.replace(microsecond=0)
    assert naive.tzinfo is None
    assert tl.parse_ts(None) is None
    assert tl.parse_ts("") is None


def test_compute_durations_accepts_postgres_aware_event_timestamps():
    events = [
        {"to_status": "pending", "ts": "2026-07-15 13:35:14.000000+00"},
        {"to_status": "active", "ts": "2026-07-15 13:36:14.000000+00"},
        {"to_status": "closed", "ts": "2026-07-15 13:38:14.000000+00"},
    ]

    durations = tl.compute_durations(events)

    assert durations["pending_sec"] == 60
    assert durations["hold_sec"] == 120
    assert durations["total_lifetime_sec"] == 180


# ── filter_log_lines ─────────────────────────────────────────────────────────

LOG_LINES = [
    "2026-06-22 10:00:00,1 [INFO] morgens",
    "2026-06-22 12:00:00,2 [ERROR] boom",
    "Traceback (most recent call last):",
    "    File x, line 1",
    "2026-06-23 09:00:00,3 [INFO] naechster tag",
]


def test_filter_log_by_day_keeps_traceback_with_parent():
    out = lf.filter_log_lines(LOG_LINES, "2026-06-22", "2026-06-22")
    assert len(out) == 4                       # 2 Logzeilen + 2 Traceback-Folgezeilen
    assert out[-1].strip().startswith("File")  # Folgezeile blieb beim 12:00-ERROR
    assert all("naechster tag" not in l for l in out)


def test_filter_log_by_level_keeps_traceback():
    out = lf.filter_log_lines(LOG_LINES, None, None, "ERROR")
    assert out[0].endswith("boom") and "Traceback" in out[1] and len(out) == 3


def test_filter_log_from_only():
    out = lf.filter_log_lines(LOG_LINES, "2026-06-23", None)
    assert out == ["2026-06-23 09:00:00,3 [INFO] naechster tag"]


# ── DB-Event-Instrumentierung ────────────────────────────────────────────────

def test_events_logged_for_lifecycle():
    _fresh()
    db.add_pending(CHAT, {"ticker": "AAA", "direction": "long", "price": 10.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "AAA", status="active")     # yf gefälscht → entry 100
    db.close_all(CHAT, [{"ticker": "AAA", "exit": 11.0, "pnl_eur": 1.0, "pnl_pct": 10.0}])

    tid = db.get_all_trades(CHAT)[0]["id"]
    evs = db.get_trade_events(tid)
    transitions = [(e["from_status"], e["to_status"]) for e in evs]
    assert (None, "pending") in transitions
    assert ("pending", "active") in transitions
    assert ("active", "closed") in transitions


def test_event_export_rows_duration():
    _fresh()
    db.add_pending(CHAT, {"ticker": "BBB", "direction": "long", "price": 10.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    tid = db.get_all_trades(CHAT)[0]["id"]
    # Zwei Events mit bekanntem Abstand (90 s) direkt schreiben.
    with db._connect() as conn:
        conn.execute("DELETE FROM trade_events WHERE trade_id = ?", (tid,))
        conn.execute("INSERT INTO trade_events (trade_id,user_id,ticker,trade_date,from_status,to_status,ts) "
                     "VALUES (?,?,?,?,?,?,?)", (tid, CHAT, "BBB", "2026-06-22", None, "pending", "2026-06-22 10:00:00"))
        conn.execute("INSERT INTO trade_events (trade_id,user_id,ticker,trade_date,from_status,to_status,ts) "
                     "VALUES (?,?,?,?,?,?,?)", (tid, CHAT, "BBB", "2026-06-22", "pending", "active", "2026-06-22 10:01:30"))
    rows = webapp._event_export_rows(CHAT, None, None)
    assert rows[0]["duration_prev_sec"] is None
    assert rows[1]["duration_prev_sec"] == 90


# ── Export-Endpoint (TestClient) ─────────────────────────────────────────────

def test_export_trades_csv_has_duration_columns():
    _fresh()
    db.add_pending(CHAT, {"ticker": "CCC", "direction": "long", "price": 10.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "CCC", status="active")
    r = _client().get("/app/backtest/export?format=csv")
    assert r.status_code == 200
    header = r.text.splitlines()[0]
    for col in ("pending_sec", "hold_sec", "total_lifetime_sec", "effective_leverage"):
        assert col in header


def test_export_date_filter_excludes_out_of_range():
    _fresh()
    db.add_pending(CHAT, {"ticker": "DDD", "direction": "long", "price": 10.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    r = _client().get("/app/backtest/export?format=csv&from=2020-01-01&to=2020-01-02")
    assert r.status_code == 200
    assert len(r.text.strip().splitlines()) == 1        # nur Header, keine Datenzeile


def test_export_events_csv():
    _fresh()
    db.add_pending(CHAT, {"ticker": "EEE", "direction": "long", "price": 10.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "EEE", status="active")
    r = _client().get("/app/backtest/export?kind=events&format=csv")
    assert r.status_code == 200
    header = r.text.splitlines()[0]
    assert "from_status" in header and "to_status" in header and "duration_prev_sec" in header


# ── Log-Endpoint (Admin-Gate) ────────────────────────────────────────────────

def _write_log(tmp):
    p = Path(tmp) / "bot.log"
    p.write_text("2026-06-22 10:00:00,1 [INFO] hallo\n2026-06-23 09:00:00,2 [ERROR] boom\n",
                 encoding="utf-8")
    return str(p)


def test_logs_forbidden_for_non_admin(monkeypatch):
    _fresh()
    monkeypatch.setattr(webapp.config, "ADMIN_CHAT_ID", 999999)   # nicht CHAT
    r = _client().get("/app/export/logs")
    assert r.status_code == 403


def test_logs_admin_download_filtered(monkeypatch, tmp_path):
    _fresh()
    monkeypatch.setattr(webapp.config, "ADMIN_CHAT_ID", CHAT)
    monkeypatch.setattr(webapp.config, "LOG_FILE", _write_log(tmp_path))
    r = _client().get("/app/export/logs?from=2026-06-23")
    assert r.status_code == 200
    assert "boom" in r.text and "hallo" not in r.text
    assert "attachment" in r.headers.get("content-disposition", "")


# ── P&L-Fix (effective_leverage) ─────────────────────────────────────────────

def test_effective_leverage_fallback_not_overstated():
    # Aktien-Fallback: leverage 5, aber effective_leverage 1 → P&L mit 1×.
    sig = {"leverage": 5.0, "effective_leverage": 1.0, "direction": "long"}
    trade = {"signal": sig, "entry": 100.0, "direction": "long"}
    _, eur = evaluator.trade_pnl(trade, 101.0, 1000.0)
    assert round(eur, 2) == 10.0          # 1% von 1000 × 1, nicht × 5


def test_effective_leverage_demo_keeps_intended():
    # Reine Demo (kein effective_leverage) → Wunsch-Hebel 5 bleibt.
    sig = {"leverage": 5.0, "direction": "long"}
    trade = {"signal": sig, "entry": 100.0, "direction": "long"}
    _, eur = evaluator.trade_pnl(trade, 101.0, 1000.0)
    assert round(eur, 2) == 50.0          # 1% × 1000 × 5
