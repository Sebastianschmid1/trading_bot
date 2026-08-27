from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from stockbot.core.domain import Mode, RiskProfile, Signal, SignalStatus, TradeIntent
from stockbot.core.market_data import Quote
from stockbot.execution import risk_context
from stockbot.execution.oms import OrderManagementSystem


class _QuoteProvider:
    """Test-Provider fuer quote_context: liefert eine Quote, ``None`` oder wirft."""

    def __init__(self, *, quote=None, raises=False):
        self._quote = quote
        self._raises = raises

    def get_quote(self, ticker):
        if self._raises:
            raise RuntimeError("offline")
        return self._quote


def _fresh_quote() -> Quote:
    now = datetime.now(timezone.utc)
    return Quote(ticker="AAPL", price=100.0, as_of=now, fetched_at=now, provider="stub")


def _signal(ticker: str = "AAPL") -> Signal:
    return Signal(
        id=17, strategy_version_id=1, ticker=ticker, direction="long", mode=Mode.PAPER,
        status=SignalStatus.ACCEPTED,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )


def _intent(key: str = "risk:42:17") -> TradeIntent:
    return TradeIntent(
        user_id=42, signal_id=17, requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="test",
        created_at=datetime.now(timezone.utc).isoformat(), idempotency_key=key,
    )


def test_signal_context_loads_positions_stop_and_market(monkeypatch):
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [
        {"ticker": "MSFT"}, {"ticker": "aapl"},
    ])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {
        "signal": {"stop_loss": 95.0},
    })

    context = risk_context.signal_context(_intent(), _signal())

    assert context["open_position_count"] == 2
    assert context["has_existing_ticker_position"] is True
    assert context["risk_profile"].user_id == 42
    assert context["stop_price"] == 95.0
    # market_open wird bewusst nicht gesetzt (Extended-Hours; siehe risk_context.py)
    assert "market_open" not in context


def test_signal_context_omits_missing_stop(monkeypatch):
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})

    assert "stop_price" not in risk_context.signal_context(_intent(), _signal())


# ── RISK-LIQUIDITY: Dollar-Umsatz-Wiring (RISK-003 Schritt 9) ───────────────

def test_signal_context_computes_average_dollar_volume_from_signal(monkeypatch):
    # avg_volume (Stueck, aus analyzer.analyze_ticker) x price (selbes Signal-JSON) -> Dollar-
    # Umsatz. Hier nur die Groessenordnung geprueft (2 Mio Stueck x 50 $ = 100 Mio $).
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {
        "signal": {"avg_volume": 2_000_000.0, "price": 50.0},
    })

    context = risk_context.signal_context(_intent(), _signal())

    assert context["average_dollar_volume"] == pytest.approx(100_000_000.0)


def test_signal_context_omits_average_dollar_volume_without_avg_volume(monkeypatch):
    # Strategien ohne avg_volume im Signal-JSON (aktuell alles außer "standard") liefern den
    # Wert nicht -> fail-open, der Liquiditaetscheck bleibt uebersprungen (heutiges Verhalten).
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {
        "signal": {"price": 50.0},
    })

    assert "average_dollar_volume" not in risk_context.signal_context(_intent(), _signal())


def test_signal_context_omits_average_dollar_volume_when_not_plausible(monkeypatch):
    # Kurs 0 (z. B. Datenluecke) -> nicht plausibel, wird weggelassen statt geraten.
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {
        "signal": {"avg_volume": 2_000_000.0, "price": 0.0},
    })

    assert "average_dollar_volume" not in risk_context.signal_context(_intent(), _signal())


def test_signal_context_average_dollar_volume_fail_open_on_read_error(monkeypatch):
    # Ausnahme beim Lesen (z. B. DB offline) -> weglassen statt haerter zu blocken.
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])

    def _boom(signal_id):
        raise RuntimeError("db offline")

    monkeypatch.setattr(risk_context.db, "get_trade_by_id", _boom)

    assert "average_dollar_volume" not in risk_context.signal_context(_intent(), _signal())


# ── RISK-005: Korrelationsgruppen-Wiring (candidate + Bestand) ───────────────

def test_signal_context_sets_candidate_correlation_group_for_known_universe(monkeypatch):
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})

    context = risk_context.signal_context(_intent(), _signal("BABA"))

    assert context["candidate_correlation_group"] == "emerging"


def test_signal_context_omits_candidate_correlation_group_for_unknown_ticker(monkeypatch):
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})

    context = risk_context.signal_context(_intent(), _signal("NOPE_XYZ"))

    assert "candidate_correlation_group" not in context   # fail-neutral: Check bleibt aus


def test_signal_context_builds_open_positions_with_correlation_group_and_notional(monkeypatch):
    trades = [
        {"ticker": "BABA", "broker_filled_qty": 10.0, "entry": 50.0},   # emerging, 500.0
        {"ticker": "PDD", "broker_filled_qty": 5.0, "entry": 100.0},    # emerging, 500.0
        {"ticker": "NOPE_XYZ", "broker_filled_qty": 1.0, "entry": 10.0},  # keine Gruppe
        {"ticker": "NOFILL", "entry": 10.0},                              # kein Fill -> raus
    ]
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: trades)
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})

    context = risk_context.signal_context(_intent(), _signal())
    positions = {p.ticker: p for p in context["open_positions"]}

    assert positions["BABA"].correlation_group == "emerging"
    assert positions["BABA"].notional == 500.0
    assert positions["PDD"].correlation_group == "emerging"
    assert positions["PDD"].notional == 500.0
    assert positions["NOPE_XYZ"].correlation_group is None
    assert "NOFILL" not in positions   # kein Broker-Fill -> kein Notional -> ausgeschlossen


def test_signal_context_open_positions_empty_without_active_trades(monkeypatch):
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})

    assert risk_context.signal_context(_intent(), _signal())["open_positions"] == ()


def _stub_signal_context_db(monkeypatch, *, stored_profile, realized_pnl):
    """Minimal-Stubs für signal_context, damit nur das Profil-/P&L-Verhalten geprüft wird."""
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    monkeypatch.setattr(risk_context.db, "get_risk_profile", lambda user_id: stored_profile)

    def _realized(user_id):
        _realized.calls += 1
        return realized_pnl

    _realized.calls = 0
    monkeypatch.setattr(risk_context.db, "get_realized_pnl_today", _realized)
    return _realized


def test_signal_context_without_stored_profile_is_inert(monkeypatch):
    # Kein gespeichertes Profil → exakt das Default-RiskProfile UND kein realized_pnl_today
    # (der Tagesverlust-Check bleibt so übersprungen; unverändertes heutiges Verhalten).
    realized = _stub_signal_context_db(monkeypatch, stored_profile=None, realized_pnl=-999.0)

    context = risk_context.signal_context(_intent(), _signal())

    assert context["risk_profile"] == RiskProfile(user_id=42)
    assert "realized_pnl_today" not in context
    assert realized.calls == 0   # ohne Profil wird der P&L gar nicht erst ermittelt


def test_signal_context_with_stored_profile_loads_it_and_daily_pnl(monkeypatch):
    stored = RiskProfile(user_id=42, daily_loss_limit_pct=2.0, max_open_positions=3)
    realized = _stub_signal_context_db(monkeypatch, stored_profile=stored, realized_pnl=-150.0)

    context = risk_context.signal_context(_intent(), _signal())

    assert context["risk_profile"] is stored
    assert context["realized_pnl_today"] == -150.0
    assert realized.calls == 1


def test_signal_context_realized_pnl_failure_is_fail_open(monkeypatch):
    stored = RiskProfile(user_id=42)
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    monkeypatch.setattr(risk_context.db, "get_risk_profile", lambda user_id: stored)

    def _boom(user_id):
        raise RuntimeError("db offline")

    monkeypatch.setattr(risk_context.db, "get_realized_pnl_today", _boom)

    context = risk_context.signal_context(_intent(), _signal())

    assert context["risk_profile"] is stored
    assert "realized_pnl_today" not in context   # fail-open: weglassen, nicht härter blocken


def test_signal_context_profile_read_failure_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})

    def _boom(user_id):
        raise RuntimeError("db offline")

    monkeypatch.setattr(risk_context.db, "get_risk_profile", _boom)

    context = risk_context.signal_context(_intent(), _signal())

    assert context["risk_profile"] == RiskProfile(user_id=42)
    assert "realized_pnl_today" not in context


def test_account_context_loads_available_account_fields():
    account = SimpleNamespace(equity="10000", buying_power="7500", status="ACTIVE",
                              cash="5000", currency="USD")
    context = risk_context.account_context(
        SimpleNamespace(get_account=lambda: account), user_id=42,
    )

    assert context == {
        "account_value": 10000.0, "buying_power": 7500.0, "broker_status": "ACTIVE",
    }
    assert "realized_pnl_today" not in context


def test_account_context_broker_failure_does_not_raise():
    def fail():
        raise RuntimeError("offline")

    assert risk_context.account_context(SimpleNamespace(get_account=fail), user_id=42) == {}


def test_quote_context_fail_open_default_on_provider_error(monkeypatch):
    # Default (Flag AUS): Providerfehler → {} (Checks werden still uebersprungen, heute).
    monkeypatch.setattr(risk_context.config, "RISK_FAIL_CLOSED_ON_QUOTE", False)
    assert risk_context.quote_context("AAPL", provider=_QuoteProvider(raises=True)) == {}


def test_quote_context_fail_open_default_on_none(monkeypatch):
    monkeypatch.setattr(risk_context.config, "RISK_FAIL_CLOSED_ON_QUOTE", False)
    assert risk_context.quote_context("AAPL", provider=_QuoteProvider(quote=None)) == {}


def test_quote_context_fail_closed_sentinel_on_provider_error(monkeypatch):
    # Flag AN: Providerfehler → Sentinel, das pretrade_check zum Blocken bewegt.
    monkeypatch.setattr(risk_context.config, "RISK_FAIL_CLOSED_ON_QUOTE", True)
    assert risk_context.quote_context(
        "AAPL", provider=_QuoteProvider(raises=True)) == {"quote_required": True}


def test_quote_context_fail_closed_sentinel_on_none(monkeypatch):
    monkeypatch.setattr(risk_context.config, "RISK_FAIL_CLOSED_ON_QUOTE", True)
    assert risk_context.quote_context(
        "AAPL", provider=_QuoteProvider(quote=None)) == {"quote_required": True}


def test_quote_context_success_identical_in_both_modes(monkeypatch):
    # Der Erfolgsfall setzt KEIN quote_required — unabhaengig vom Flag.
    provider = _QuoteProvider(quote=_fresh_quote())
    monkeypatch.setattr(risk_context.config, "RISK_FAIL_CLOSED_ON_QUOTE", True)
    ctx_on = risk_context.quote_context("AAPL", provider=provider)
    monkeypatch.setattr(risk_context.config, "RISK_FAIL_CLOSED_ON_QUOTE", False)
    ctx_off = risk_context.quote_context("AAPL", provider=provider)
    assert ctx_on == ctx_off
    assert "quote_required" not in ctx_on
    assert ctx_on["quote"].ticker == "AAPL"


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


def test_real_oms_applies_loaded_position_limit_and_allows_clean_case(monkeypatch):
    active = [{"ticker": f"T{i}"} for i in range(5)]
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: active)
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    service = OrderManagementSystem(
        signal_loader=lambda signal_id: _signal(), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )
    callsite_context = {"entry_price": 100.0, "candidate_notional": 100.0}

    rejected = service.submit_intent(
        _intent(), price=100.0, trade_size=100.0, risk_context=callsite_context,
    )
    assert rejected.ok is False and rejected.code == "max_positions_reached"

    active.clear()
    accepted = service.submit_intent(
        _intent("risk:42:18"), price=100.0, trade_size=100.0,
        risk_context=callsite_context,
    )
    assert accepted.ok is True


def _oms_service(monkeypatch=None):
    # Die fail-closed-Tests reichen `monkeypatch` durch, damit der Loader keine echte DB
    # anspricht; die Profil-Inertness-Tests patchen selbst inline und rufen ohne Argument.
    if monkeypatch is not None:
        monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
        monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    return OrderManagementSystem(
        signal_loader=lambda signal_id: _signal(), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )


def test_real_oms_daily_loss_limit_inert_without_stored_profile(monkeypatch):
    # Kern der Inert-by-default-Garantie: ein Nutzer OHNE gespeichertes Profil bekommt trotz
    # eines heftigen realisierten Tagesverlusts dieselbe (erlaubende) Gate-Entscheidung wie
    # vor dem Change — realized_pnl_today wird gar nicht durchgereicht.
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    monkeypatch.setattr(risk_context.db, "get_risk_profile", lambda user_id: None)
    monkeypatch.setattr(risk_context.db, "get_realized_pnl_today", lambda user_id: -9999.0)

    accepted = _oms_service().submit_intent(
        _intent("risk:42:19"), price=100.0, trade_size=100.0,
        risk_context={"account_value": 10000.0, "candidate_notional": 100.0},
    )
    assert accepted.ok is True


def test_real_oms_daily_loss_limit_bites_with_stored_profile(monkeypatch):
    # Mit gespeichertem Profil (Opt-in) greift das Tagesverlustlimit: -500 realisiert bei
    # 10.000 Kontowert und 1% Limit (=100) blockt neue Positionen.
    stored = RiskProfile(user_id=42, daily_loss_limit_pct=1.0)
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    monkeypatch.setattr(risk_context.db, "get_risk_profile", lambda user_id: stored)
    monkeypatch.setattr(risk_context.db, "get_realized_pnl_today", lambda user_id: -500.0)

    rejected = _oms_service().submit_intent(
        _intent("risk:42:20"), price=100.0, trade_size=100.0,
        risk_context={"account_value": 10000.0, "candidate_notional": 100.0},
    )
    assert rejected.ok is False and rejected.code == "daily_loss_limit_reached"


# ── RISK-005: Korrelations-Exposure Ende-zu-Ende (echter OMS-Pfad) ───────────

def test_real_oms_correlated_exposure_default_profile_allows_like_before(monkeypatch):
    # Ohne gespeichertes Profil bleibt der 100%-Default-Deckel wirkungslos (Aus-Schalter) — eine
    # Order, die heute durchgeht, geht trotz vorhandenem Bestand in derselben Gruppe weiter durch.
    active = [{"ticker": "BABA", "broker_filled_qty": 50.0, "entry": 100.0}]   # emerging, 5.000
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: active)
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    monkeypatch.setattr(risk_context.db, "get_risk_profile", lambda user_id: None)
    service = OrderManagementSystem(
        signal_loader=lambda signal_id: _signal("PDD"), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )

    result = service.submit_intent(
        _intent("risk:42:30"), price=100.0, trade_size=100.0,
        risk_context={"entry_price": 100.0, "candidate_notional": 4000.0, "account_value": 10_000.0},
    )

    assert result.ok is True


def test_real_oms_correlated_exposure_blocks_when_profile_lowers_limit(monkeypatch):
    # Gesenktes Limit (40%) macht den Check scharf: Bestand 5.000 + Kandidat 2.000 = 7.000 über
    # 40% von 10.000 (=4.000) -> abgelehnt mit dem vorhandenen exposure.py-Ablehngrund.
    stored = RiskProfile(user_id=42, max_correlated_exposure_pct=40.0)
    active = [{"ticker": "BABA", "broker_filled_qty": 50.0, "entry": 100.0}]   # emerging, 5.000
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: active)
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    monkeypatch.setattr(risk_context.db, "get_risk_profile", lambda user_id: stored)
    monkeypatch.setattr(risk_context.db, "get_realized_pnl_today", lambda user_id: 0.0)
    service = OrderManagementSystem(
        signal_loader=lambda signal_id: _signal("PDD"), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )

    result = service.submit_intent(
        _intent("risk:42:31"), price=100.0, trade_size=100.0,
        risk_context={"entry_price": 100.0, "candidate_notional": 2000.0, "account_value": 10_000.0},
    )

    assert result.ok is False and result.code == "correlated_exposure"


def test_real_oms_correlated_exposure_stays_inert_for_unmapped_ticker(monkeypatch):
    # Kandidat-Ticker ohne Gruppenzuordnung (keine der statischen Universen-Listen) lässt den
    # Check aus wie bisher -- selbst mit einem sehr scharfen Limit.
    stored = RiskProfile(user_id=42, max_correlated_exposure_pct=1.0)
    active = [{"ticker": "BABA", "broker_filled_qty": 50.0, "entry": 100.0}]
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: active)
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {"signal": {}})
    monkeypatch.setattr(risk_context.db, "get_risk_profile", lambda user_id: stored)
    monkeypatch.setattr(risk_context.db, "get_realized_pnl_today", lambda user_id: 0.0)
    service = OrderManagementSystem(
        signal_loader=lambda signal_id: _signal("NOPE_XYZ"), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )

    result = service.submit_intent(
        _intent("risk:42:32"), price=100.0, trade_size=100.0,
        risk_context={"entry_price": 100.0, "candidate_notional": 2000.0, "account_value": 10_000.0},
    )

    assert result.ok is True


def test_oms_fail_open_default_lets_missing_quote_through(monkeypatch):
    # Regression/Inertness: OHNE gesetztes Flag laeuft die Order trotz Quote-Fehler durch —
    # das Sentinel entsteht gar nicht erst, es gibt KEINE Verhaltensaenderung.
    monkeypatch.setattr(risk_context.config, "RISK_FAIL_CLOSED_ON_QUOTE", False)
    service = _oms_service(monkeypatch)
    callsite = {"entry_price": 100.0, "candidate_notional": 100.0,
                **risk_context.quote_context("AAPL", provider=_QuoteProvider(raises=True))}
    result = service.submit_intent(
        _intent(), price=100.0, trade_size=100.0, risk_context=callsite)
    assert result.ok is True


def test_oms_fail_closed_blocks_missing_quote(monkeypatch):
    # Flag AN: derselbe Quote-Fehler blockt die Order mit maschinenlesbarem Grund.
    monkeypatch.setattr(risk_context.config, "RISK_FAIL_CLOSED_ON_QUOTE", True)
    service = _oms_service(monkeypatch)
    callsite = {"entry_price": 100.0, "candidate_notional": 100.0,
                **risk_context.quote_context("AAPL", provider=_QuoteProvider(raises=True))}
    result = service.submit_intent(
        _intent("risk:42:19"), price=100.0, trade_size=100.0, risk_context=callsite)
    assert result.ok is False and result.code == "quote_unavailable"


def test_oms_fail_closed_allows_good_quote(monkeypatch):
    # Flag AN, aber Quote vorhanden → unveraendert durchgelassen.
    monkeypatch.setattr(risk_context.config, "RISK_FAIL_CLOSED_ON_QUOTE", True)
    service = _oms_service(monkeypatch)
    callsite = {"entry_price": 100.0, "candidate_notional": 100.0,
                **risk_context.quote_context("AAPL", provider=_QuoteProvider(quote=_fresh_quote()))}
    result = service.submit_intent(
        _intent("risk:42:20"), price=100.0, trade_size=100.0, risk_context=callsite)
    assert result.ok is True


# ── RISK-LIQUIDITY Ende-zu-Ende (echter OMS-Pfad) ────────────────────────────

def test_real_oms_liquidity_default_threshold_allows_like_before(monkeypatch):
    # average_dollar_volume kommt jetzt echt aus signal_context (avg_volume x price aus dem
    # Trade); bei der ausgelieferten Default-Schwelle 0.0 bleibt der Check trotzdem wirkungslos
    # (0.0 < 0.0 ist nie wahr) -> unveraendertes Verhalten.
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {
        "signal": {"avg_volume": 1_000.0, "price": 5.0},   # 5.000 $ -- sehr geringer Umsatz
    })
    monkeypatch.setattr(risk_context.db, "get_risk_profile", lambda user_id: None)
    service = OrderManagementSystem(
        signal_loader=lambda signal_id: _signal(), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )

    result = service.submit_intent(
        _intent("risk:42:40"), price=100.0, trade_size=100.0,
        risk_context={"entry_price": 100.0, "candidate_notional": 100.0,
                      "min_average_dollar_volume": RiskProfile(user_id=42).min_average_dollar_volume},
    )

    assert result.ok is True


def test_real_oms_liquidity_blocks_when_threshold_set_above_value(monkeypatch):
    # Dieselbe (sehr geringe) average_dollar_volume wie oben, aber mit einer bewusst gesetzten
    # Schwelle darueber -> der Check greift jetzt tatsaechlich (Beweis, dass die Verdrahtung
    # wirkt, nicht nur dass sie existiert).
    monkeypatch.setattr(risk_context.db, "get_active_trades", lambda user_id: [])
    monkeypatch.setattr(risk_context.db, "get_trade_by_id", lambda signal_id: {
        "signal": {"avg_volume": 1_000.0, "price": 5.0},   # 5.000 $
    })
    monkeypatch.setattr(risk_context.db, "get_risk_profile", lambda user_id: None)
    service = OrderManagementSystem(
        signal_loader=lambda signal_id: _signal(), context_loader=risk_context.signal_context,
        broker_adapter=_Broker(), persistence=_Persistence(),
    )

    result = service.submit_intent(
        _intent("risk:42:41"), price=100.0, trade_size=100.0,
        risk_context={"entry_price": 100.0, "candidate_notional": 100.0,
                      "min_average_dollar_volume": 100_000.0},
    )

    assert result.ok is False and result.code == "liquidity_low"
