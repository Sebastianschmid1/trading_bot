"""
Tests für die Alpaca-Anbindung (broker.py). Komplett offline — ohne Netz, ohne echte Keys.
Ein Fake-Client ersetzt den Alpaca TradingClient; der echte SDK-Import wird nicht gebraucht,
weil alle Funktionen einen `client=`-Parameter akzeptieren.

Lauf:  python test_broker.py   oder   pytest test_broker.py
"""

import sys

import broker
import config


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
    def __init__(self, *, fail=False):
        self.fail = fail
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
    def get_all_positions(self):
        if self.fail: raise RuntimeError("boom")
        class _P:
            symbol = "AAPL"; qty = "3"; avg_entry_price = "100.0"; unrealized_pl = "5.0"
        return [_P()]
    def close_position(self, symbol):
        if self.fail: raise RuntimeError("boom")
        self.closed.append(symbol)


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


# ── submit_order ─────────────────────────────────────────────────────────────

def test_submit_order_regular_builds_bracket():
    c = FakeClient()
    res = broker.submit_order("AAPL", 5, 100.0, 95.0, 110.0, client=c)
    assert res["ok"] is True
    assert res["id"] == "order-123"
    assert len(c.submitted) == 1
    # Bracket-Order in regulärer Zeit
    assert "Bracket" in res["detail"]


def test_submit_order_extended_uses_limit_day():
    c = FakeClient()
    res = broker.submit_order("AAPL", 5, 100.0, 95.0, 110.0, extended_hours=True, client=c)
    assert res["ok"] is True
    assert "Ext" in res["detail"]
    req = c.submitted[0]
    assert getattr(req, "extended_hours", False) is True
    assert getattr(req, "limit_price", None) == 100.0


def test_submit_order_error_is_handled():
    res = broker.submit_order("AAPL", 5, 100.0, 95.0, 110.0, client=FakeClient(fail=True))
    assert res["ok"] is False
    assert "RuntimeError" in res["detail"]


def test_submit_order_no_client_is_off():
    # ohne Client und ohne aktiviertes Alpaca → sauberes ok:False
    orig = config.ALPACA_ENABLED
    config.ALPACA_ENABLED = False
    try:
        res = broker.submit_order("AAPL", 5, 100.0, 95.0, 110.0)
        assert res["ok"] is False
    finally:
        config.ALPACA_ENABLED = orig


# ── Positionen ───────────────────────────────────────────────────────────────

def test_list_positions():
    pos = broker.list_positions(client=FakeClient())
    assert len(pos) == 1 and pos[0]["symbol"] == "AAPL" and pos[0]["qty"] == 3.0


def test_close_position():
    c = FakeClient()
    res = broker.close_position("AAPL", client=c)
    assert res["ok"] is True and c.closed == ["AAPL"]


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
