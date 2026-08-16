"""
Tests für die Alpaca-Anbindung (broker.py). Komplett offline — ohne Netz, ohne echte Keys.
Ein Fake-Client ersetzt den Alpaca TradingClient; der echte SDK-Import wird nicht gebraucht,
weil alle Funktionen einen `client=`-Parameter akzeptieren.

Lauf:  python test_broker.py   oder   pytest test_broker.py
"""

import sys
from types import SimpleNamespace

import requests

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
    def __init__(self, *, fail=False, no_position=False, order_status="filled",
                position_symbol="AAPL"):
        self.fail = fail
        self.no_position = no_position
        self.order_status = order_status
        self.position_symbol = position_symbol
        self.submitted = []
        self.closed = []
        self.get_open_position_calls = []
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
            pass
        p = _P()
        p.symbol, p.qty, p.avg_entry_price, p.unrealized_pl = (
            self.position_symbol, "3", "100.0", "5.0")
        return [p]
    def get_open_position(self, symbol):
        self.get_open_position_calls.append(symbol)
        if self.fail: raise RuntimeError("boom")
        if self.no_position: raise RuntimeError("position does not exist for symbol")
        return SimpleNamespace(symbol=symbol, qty="3", avg_entry_price="100.0",
                               unrealized_pl="5.0")
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


# ── Optionshandel deaktiviert (Phase 0 / TSAFE-003) ──────────────────────────

def test_option_order_blocked_when_options_disabled():
    orig = config.ALLOW_OPTIONS
    config.ALLOW_OPTIONS = False
    try:
        c = _PaperClient()
        res = broker.submit_option_buy("AAPL240712C00150000", 1, client=c)
        assert res["ok"] is False and "Option" in res["detail"]
        assert c.submitted == []
    finally:
        config.ALLOW_OPTIONS = orig


def test_option_order_allowed_when_options_enabled():
    orig_o, orig_l = config.ALLOW_OPTIONS, config.LIVE_TRADING_ENABLED
    config.ALLOW_OPTIONS = True
    config.LIVE_TRADING_ENABLED = True     # Live-Gate separat erfüllen
    try:
        c = _PaperClient()
        res = broker.submit_option_buy("AAPL240712C00150000", 1, client=c)
        assert res["ok"] is True and len(c.submitted) == 1
    finally:
        config.ALLOW_OPTIONS, config.LIVE_TRADING_ENABLED = orig_o, orig_l


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


# ── Symbol-Mapping an der Alpaca-Grenze (Klassen-Suffixe BRK-B ↔ BRK.B) ──────

def test_to_alpaca_symbol_maps_hyphen_class_suffix_to_dot():
    assert broker.to_alpaca_symbol("BRK-B") == "BRK.B"
    assert broker.to_alpaca_symbol("BF-B") == "BF.B"


def test_to_bot_symbol_maps_dot_class_suffix_to_hyphen():
    assert broker.to_bot_symbol("BRK.B") == "BRK-B"
    assert broker.to_bot_symbol("BF.B") == "BF-B"


def test_symbol_mapping_plain_ticker_unchanged_both_directions():
    # AC5: normale Ticker ohne Suffix bleiben in beiden Richtungen exakt gleich.
    assert broker.to_alpaca_symbol("AAPL") == "AAPL"
    assert broker.to_bot_symbol("AAPL") == "AAPL"


def test_symbol_mapping_occ_option_symbol_untouched():
    # AC4: OCC-Optionssymbole (Root+YYMMDD+C/P+8-stelliger Strike) bleiben unverändert — sie
    # enthalten strukturell weder Bindestrich noch Punkt.
    occ = "AAPL260116C00200000"
    assert broker.to_alpaca_symbol(occ) == occ
    assert broker.to_bot_symbol(occ) == occ
    occ_put = "SPY251219P00450500"
    assert broker.to_alpaca_symbol(occ_put) == occ_put
    assert broker.to_bot_symbol(occ_put) == occ_put


def test_symbol_mapping_roundtrip_class_suffix():
    assert broker.to_bot_symbol(broker.to_alpaca_symbol("BRK-B")) == "BRK-B"
    assert broker.to_alpaca_symbol(broker.to_bot_symbol("BRK.B")) == "BRK.B"


# ── AC1/AC2: Hinrichtung bei Orders (Kauf/Verkauf, Market/Limit) ─────────────

def test_submit_buy_market_sends_alpaca_dot_symbol():
    """AC2 (Kauf, Market): BRK-B geht als BRK.B an Alpaca raus."""
    c = FakeClient()
    res = broker.submit_buy("BRK-B", notional=50.0, client=c)
    assert res["ok"] is True
    req = c.submitted[0]
    assert getattr(req, "symbol") == "BRK.B"


def test_submit_buy_extended_limit_sends_alpaca_dot_symbol():
    """AC2 (Kauf, Limit/Extended Hours): BF-B geht als BF.B an Alpaca raus."""
    c = FakeClient()
    res = broker.submit_buy("BF-B", qty=2, limit_price=50.0, extended_hours=True, client=c)
    assert res["ok"] is True
    req = c.submitted[0]
    assert getattr(req, "symbol") == "BF.B"


def test_submit_exit_order_sell_market_sends_alpaca_dot_symbol():
    """AC2 (Verkauf, Market): BRK-B geht als BRK.B an Alpaca raus."""
    c = FakeClient()
    res = broker.submit_exit_order("BRK-B", side="SELL", qty=1, client=c)
    assert res["ok"] is True
    req = c.submitted[0]
    assert getattr(req, "symbol") == "BRK.B"


def test_submit_exit_order_sell_limit_sends_alpaca_dot_symbol():
    """AC2 (Verkauf, Limit): BRK-B geht als BRK.B an Alpaca raus."""
    c = FakeClient()
    res = broker.submit_exit_order("BRK-B", side="SELL", qty=1, limit_price=300.0, client=c)
    assert res["ok"] is True
    req = c.submitted[0]
    assert getattr(req, "symbol") == "BRK.B"


def test_submit_stop_sell_sends_alpaca_dot_symbol():
    c = FakeClient()
    res = broker.submit_stop_sell("BRK-B", qty=1, stop_price=300.0, client=c)
    assert res["ok"] is True
    req = c.submitted[0]
    assert getattr(req, "symbol") == "BRK.B"


def test_close_position_sends_alpaca_dot_symbol():
    c = FakeClient()
    res = broker.close_position("BRK-B", client=c)
    assert res["ok"] is True and res["closed"] is True
    assert c.closed == ["BRK.B"]


def test_position_exists_queries_alpaca_dot_symbol():
    c = FakeClient()
    assert broker.position_exists("BRK-B", client=c) is True
    assert c.get_open_position_calls == ["BRK.B"]


# ── Rückrichtung beim Einlesen von Positionen/Orders aus Alpaca-Antworten ────

def test_list_positions_maps_alpaca_dot_symbol_back_to_bot_hyphen():
    pos = broker.list_positions(client=FakeClient(position_symbol="BRK.B"))
    assert len(pos) == 1 and pos[0]["symbol"] == "BRK-B"


def test_get_position_maps_alpaca_dot_symbol_back_to_bot_hyphen():
    pos = broker.get_position("BRK-B", client=FakeClient())
    assert pos["symbol"] == "BRK-B"


# ── BROKER-TIMEOUTS: hängende Alpaca-Calls dürfen den 60s-Job nicht sprengen ─
#
# Kontext: monitor_trades wurde am Live-VPS gelegentlich als "maximum number of running
# instances reached" übersprungen — die Skips korrelierten exakt mit Alpaca-Timeouts
# ("request timed out"), weil kein Alpaca-Client je ein HTTP-Timeout gesetzt hatte. Die
# installierte alpaca-py-Version (RESTClient in alpaca/common/rest.py) hat dafür keinen
# Konstruktor-Parameter — `apply_http_timeout()` tauscht stattdessen `_session` gegen eine
# Session mit erzwungenem Default-Timeout aus.

def test_make_client_attaches_timeout_session_with_configured_values():
    c = broker.make_client("key", "secret", paper=True)
    assert isinstance(c._session, broker._TimeoutSession)
    assert c._session._default_timeout == (config.ALPACA_CONNECT_TIMEOUT, config.ALPACA_READ_TIMEOUT)


def test_timeout_session_passes_configured_timeout_into_real_request_call(monkeypatch):
    """Prüft die tatsächlich übergebene Konfiguration end-to-end: über den echten TradingClient
    bis zum darunterliegenden `requests.Session.request()`-Aufruf — nicht nur, dass irgendwo eine
    Konstante mit passendem Namen existiert."""
    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured.update(kwargs)
        raise requests.exceptions.ReadTimeout("request timed out")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    c = broker.make_client("key", "secret", paper=True)
    try:
        c.get_clock()
    except requests.exceptions.ReadTimeout:
        pass
    assert captured.get("timeout") == (config.ALPACA_CONNECT_TIMEOUT, config.ALPACA_READ_TIMEOUT)


def test_list_positions_timeout_is_fail_neutral_not_uncaught(monkeypatch, caplog):
    """Ein Timeout beim Positionsabruf darf weiterhin nur zu {ok:.., []} + Warnung führen
    (bestehendes fail-neutrales Verhalten aus list_positions) — nicht zu einer neuen,
    ungefangenen Exception."""
    def _raise_timeout(self, *a, **kw):
        raise requests.exceptions.ReadTimeout('{"code":N,"message":"request timed out"}')

    monkeypatch.setattr(requests.Session, "request", _raise_timeout)
    c = broker.make_client("key", "secret", paper=True)
    with caplog.at_level("WARNING"):
        result = broker.list_positions(client=c)
    assert result == []
    assert "Alpaca-Positionen nicht abrufbar" in caplog.text


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
