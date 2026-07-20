from datetime import datetime, timedelta, timezone

import pytest

from stockbot.core.domain import Mode, Signal, SignalStatus, TradeIntent
from stockbot.core.market_data import Quote
from stockbot.execution import risk_context
from stockbot.execution.oms import OrderManagementSystem


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


class _QuoteProvider:
    def __init__(self, quote=None, error=None):
        self.quote = quote
        self.error = error

    def get_quote(self, ticker):
        if self.error:
            raise self.error
        return self.quote


def _quote(*, age_seconds=0, bid=100.0, ask=100.1):
    return Quote(
        ticker="AAPL", price=(bid + ask) / 2, as_of=NOW - timedelta(seconds=age_seconds),
        fetched_at=NOW, provider="fake", bid=bid, ask=ask,
    )


def test_quote_context_returns_quote_and_profile_limits():
    quote = _quote()

    context = risk_context.quote_context("AAPL", provider=_QuoteProvider(quote))

    assert context == {
        "quote": quote, "max_quote_age_seconds": 60, "max_spread_bps": 50.0,
    }


@pytest.mark.parametrize("provider", [
    _QuoteProvider(error=RuntimeError("offline")),
    _QuoteProvider(quote=None),
])
def test_quote_context_provider_failure_is_fail_open(provider):
    # Der Fail-open-Contract ist der Rueckgabewert {} (Provider-Fehler/leere Quote
    # blockieren die Order NICHT, der Check skippt). Bewusst NICHT ueber caplog geprueft:
    # bot.py ruft beim Import logging.basicConfig() → in der Vollsuite faengt caplog die
    # WARN-Meldung des Modul-Loggers nicht zuverlaessig ein (propagation-/Handler-abhaengig).
    assert risk_context.quote_context("AAPL", provider=provider) == {}


class _Persistence:
    def __init__(self):
        self.orders = {}

    def get_order_by_idempotency_key(self, key):
        return None

    def create_oms_order(self, intent, **kwargs):
        row = {
            "id": 1, "trade_intent_id": 1, "user_id": intent.user_id,
            "ticker": kwargs["ticker"], "side": "buy", "qty": kwargs["qty"],
            "notional": kwargs["notional"], "limit_price": kwargs["limit_price"],
            "status": "created", "broker_order_id": None, "client_order_id": "oms-1",
            "idempotency_key": intent.idempotency_key, "created_at": None, "updated_at": None,
        }
        self.orders[1] = row
        return row, True

    def transition_oms_order(self, order_id, *, to_status, **kwargs):
        self.orders[order_id].update(status=to_status)
        if kwargs.get("broker_order_id"):
            self.orders[order_id]["broker_order_id"] = kwargs["broker_order_id"]
        return self.orders[order_id]


class _Broker:
    def submit_buy(self, symbol, **kwargs):
        return {"ok": True, "id": "paper-1", "status": "filled"}


def _signal():
    return Signal(
        id=17, strategy_version_id=1, ticker="AAPL", direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED, expires_at=(NOW + timedelta(minutes=5)).isoformat(),
    )


def _intent(key):
    return TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="test",
        created_at=NOW.isoformat(), idempotency_key=key,
    )


@pytest.mark.parametrize(("quote", "expected_ok", "expected_code"), [
    (_quote(age_seconds=61), False, "quote_stale"),
    (_quote(), True, ""),
    (_quote(bid=100.0, ask=101.0), False, "spread_wide"),
])
def test_real_oms_applies_quote_quality_gates(monkeypatch, quote, expected_ok, expected_code):
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    service = OrderManagementSystem(
        signal_loader=lambda signal_id: _signal(), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )
    callsite_context = {
        "entry_price": 100.0, "candidate_notional": 100.0, "now": NOW,
        **risk_context.quote_context("AAPL", provider=_QuoteProvider(quote)),
    }

    result = service.submit_intent(
        _intent(f"quote:{expected_code or 'fresh'}"), price=100.0, trade_size=100.0,
        risk_context=callsite_context,
    )

    assert result.ok is expected_ok
    assert result.code == expected_code


@pytest.mark.parametrize(("now", "expected_code"), [
    (NOW, ""),                              # Signal laeuft erst in 5 Minuten ab
    (NOW + timedelta(minutes=10), "signal_expired"),
])
def test_signal_expiry_uses_the_callsite_clock_not_the_wall_clock(monkeypatch, now, expected_code):
    """Der Ablaufcheck folgt `now` aus dem Risk-Kontext.

    Ohne das lief die Ablaufpruefung gegen die echte Wanduhr — jeder Test mit fixem
    Zeitpunkt wurde nach seinem Ablaufdatum rot (Wall-Clock-Leak). Prod uebergibt kein
    `now` und bleibt damit unveraendert auf der Wanduhr.
    """
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    service = OrderManagementSystem(
        signal_loader=lambda signal_id: _signal(), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )

    result = service.submit_intent(
        _intent(f"clock:{expected_code or 'fresh'}"), price=100.0, trade_size=100.0,
        risk_context={"entry_price": 100.0, "candidate_notional": 100.0, "now": now,
                      **risk_context.quote_context("AAPL", provider=_QuoteProvider(_quote()))},
    )

    assert result.code == expected_code
