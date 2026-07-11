"""
Tests für die Alpaca-Anbindung (broker.py). Komplett offline — ohne Netz, ohne echte Keys.
Ein Fake-Client ersetzt den Alpaca TradingClient; der echte SDK-Import wird nicht gebraucht,
weil alle Funktionen einen `client=`-Parameter akzeptieren.

Lauf:  python test_broker.py   oder   pytest test_broker.py
"""

import sys
from types import SimpleNamespace

from stockbot.broker import client as broker
from stockbot import config


# ── Fake-Alpaca-Client ───────────────────────────────────────────────────────

class _Acct:
    status = "ACTIVE"
    cash = "10000.00"
    buying_power = "20000.00"
    currency = "USD"

class _Clock:
    is_open = True
    next_open = "2026-06-12T13:30:00Z"
    next_close = "2026-06-11T20:00:00Z"

class _Order:
    id = "order-123"

class FakeClient:
    """Minimaler Stand-in für den Alpaca TradingClient."""
    def __init__(self, *, fail=False, no_position=False, order_status="filled"):
        self.fail = fail
        self.no_position = no_position
        self.order_status = order_status
        self.submitted = []
        self.closed = []
    def get_account(self):
        if self.fail: raise RuntimeError("boom")
        return _Acct()
    def get_clock(self):
        if self.fail: raise RuntimeError("boom")
        return _Clock()
    def submit_order(self, req):
        if self.fail: raise RuntimeError("rejected")
        self.submitted.append(req)
        return _Order()
    def get_order_by_id(self, order_id):
        if self.fail: raise RuntimeError("boom")
        return SimpleNamespace(id=order_id, status=self.order_status,
                               filled_qty="0.333", filled_avg_price="150.0")
    def get_all_positions(self):
        if self.fail: raise RuntimeError("boom")
        class _P:
            symbol = "AAPL"; qty = "3"; avg_entry_price = "100.0"; unrealized_pl = "5.0"
        return [_P()]
    def close_position(self, symbol):
        if self.fail: raise RuntimeError("boom")
        if self.no_position: raise RuntimeError("position does not exist for symbol")
        self.closed.append(symbol)
        return _Order()


# ── health_check ─────────────────────────────────────────────────────────────

def test_health_check_no_keys_is_off():
    orig = config.ALPACA_ENABLED
    config.ALPACA_ENABLED = False
    try:
        res = broker.health_check()
        assert res["ok"] is False
        assert "aus" in res["detail"].lower() or "ALPACA" in res["detail"]
    finally:
        config.ALPACA_ENABLED = orig


def test_health_check_with_fake_client_ok():
    res = broker.health_check(client=FakeClient())
    assert res["ok"] is True
    assert res["status"] == "ACTIVE"
    assert res["cash"] == 10000.0
    assert res["market_open"] is True


def test_health_check_client_error_is_handled():
    res = broker.health_check(client=FakeClient(fail=True))
    assert res["ok"] is False
    assert "RuntimeError" in res["detail"]


# ── market_open ──────────────────────────────────────────────────────────────

def test_market_open_reads_clock():
    assert broker.market_open(client=FakeClient()) is True


def test_market_open_error_returns_none():
    assert broker.market_open(client=FakeClient(fail=True)) is None


# ── submit_buy ───────────────────────────────────────────────────────────────

def test_submit_buy_regular_uses_notional_fractional():
    c = FakeClient()
    res = broker.submit_buy("AAPL", notional=50.0, client=c)
    assert res["ok"] is True
    assert res["id"] == "order-123"
    assert "Bruchteile" in res["detail"]
    req = c.submitted[0]
    assert float(getattr(req, "notional")) == 50.0       # exakt fürs Budget, keine ganze Aktie nötig


def test_submit_buy_extended_uses_limit_day_whole_shares():
    c = FakeClient()
    res = broker.submit_buy("AAPL", qty=2, limit_price=150.0, extended_hours=True, client=c)
    assert res["ok"] is True
    assert "Ext" in res["detail"]
    req = c.submitted[0]
    assert getattr(req, "extended_hours", False) is True
    assert float(getattr(req, "limit_price")) == 150.0
    assert float(getattr(req, "qty")) == 2


def test_submit_buy_extended_needs_qty_and_limit():
    res = broker.submit_buy("AAPL", extended_hours=True, client=FakeClient())
    assert res["ok"] is False


def test_submit_buy_error_is_handled():
    res = broker.submit_buy("AAPL", notional=50.0, client=FakeClient(fail=True))
    assert res["ok"] is False
    assert "RuntimeError" in res["detail"]


def test_submit_buy_no_client_is_off():
    orig = config.ALPACA_ENABLED
    config.ALPACA_ENABLED = False
    try:
        res = broker.submit_buy("AAPL", notional=50.0)
        assert res["ok"] is False
    finally:
        config.ALPACA_ENABLED = orig


# ── Globaler Live-Kill-Switch (Phase 0 / TSAFE-001) ──────────────────────────

class _LiveClient(FakeClient):
    """Fake-Client auf einem echten Geldkonto (Paper-Flag aus)."""
    _paper = False

class _PaperClient(FakeClient):
    _paper = True


def test_live_order_blocked_when_live_disabled():
    """Eine Order gegen ein Live-Konto wird abgelehnt, solange Live nicht freigeschaltet ist."""
    orig = config.LIVE_TRADING_ENABLED
    config.LIVE_TRADING_ENABLED = False
    try:
        c = _LiveClient()
        res = broker.submit_buy("AAPL", notional=50.0, client=c)
        assert res["ok"] is False
        assert "Live-Trading" in res["detail"] or "Kill-Switch" in res["detail"]
        assert c.submitted == []                      # keine Order beim Broker gelandet
        # auch der Options-Einstieg ist gesperrt
        res_opt = broker.submit_option_buy("AAPL240712C00150000", 1, client=c)
        assert res_opt["ok"] is False
    finally:
        config.LIVE_TRADING_ENABLED = orig


def test_paper_order_allowed_when_live_disabled():
    """Paper-Orders laufen weiter, auch wenn Live global aus ist."""
    orig = config.LIVE_TRADING_ENABLED
    config.LIVE_TRADING_ENABLED = False
    try:
        c = _PaperClient()
        res = broker.submit_buy("AAPL", notional=50.0, client=c)
        assert res["ok"] is True and len(c.submitted) == 1
    finally:
        config.LIVE_TRADING_ENABLED = orig


def test_live_order_allowed_when_live_enabled():
    """Ist Live ausdrücklich freigeschaltet, wird die Live-Order durchgelassen."""
    orig = config.LIVE_TRADING_ENABLED
    config.LIVE_TRADING_ENABLED = True
    try:
        c = _LiveClient()
        res = broker.submit_buy("AAPL", notional=50.0, client=c)
        assert res["ok"] is True and len(c.submitted) == 1
    finally:
        config.LIVE_TRADING_ENABLED = orig


# ── get_order_status (Fill-Bestätigung) ──────────────────────────────────────

def test_get_order_status_filled():
    res = broker.get_order_status("order-123", client=FakeClient(order_status="filled"))
    assert res["ok"] is True and res["status"] == "filled"
    assert res["filled_qty"] == 0.333 and res["filled_avg_price"] == 150.0


def test_get_order_status_accepted_not_filled():
    res = broker.get_order_status("order-123", client=FakeClient(order_status="accepted"))
    assert res["ok"] is True and res["status"] == "accepted"


# ── Positionen / Schließen ────────────────────────────────────────────────────

def test_list_positions():
    pos = broker.list_positions(client=FakeClient())
    assert len(pos) == 1 and pos[0]["symbol"] == "AAPL" and pos[0]["qty"] == 3.0


def test_close_position_closes():
    c = FakeClient()
    res = broker.close_position("AAPL", client=c)
    assert res["ok"] is True and res["closed"] is True and c.closed == ["AAPL"]


def test_close_position_no_open_position_is_ok_noop():
    res = broker.close_position("AAPL", client=FakeClient(no_position=True))
    assert res["ok"] is True and res["closed"] is False     # nichts offen → kein Fehler


def test_close_position_error_is_handled():
    res = broker.close_position("AAPL", client=FakeClient(fail=True))
    assert res["ok"] is False and res["closed"] is False


# ── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
