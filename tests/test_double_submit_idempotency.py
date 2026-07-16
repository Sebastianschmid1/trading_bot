"""E2E-Beweis: Doppelte Accept-Aktionen erzeugen nur eine Broker-Order."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient

from stockbot.core import db
from stockbot.tgbot import bot
from stockbot.web import auth
from stockbot.web import dashboard as _dashboard  # noqa: F401; verhindert Zirkularimport
from stockbot.web import webapp


WEB_CHAT = 4242
TELEGRAM_CHAT = 5151


class _FakeYF:
    def __init__(self, price):
        self._price = price

    def Ticker(self, ticker):
        price = self._price

        class _Ticker:
            @property
            def fast_info(self):
                class _FastInfo:
                    last_price = price

                return _FastInfo()

        return _Ticker()


def _fresh_web():
    directory = tempfile.mkdtemp(prefix="webtest_")
    db.DB_FILE = Path(directory) / "web.db"
    db.init_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(WEB_CHAT, "webtester")
    db.save_profile(WEB_CHAT, trade_size_eur=100.0)
    auth._auth_hits.clear()
    webapp._scan_cache.clear()


def _web_client():
    client = TestClient(_dashboard.app)
    token = db.get_or_create_dashboard_token(WEB_CHAT)
    client.get(f"/auth/token?token={token}")
    return client


def _fresh_telegram():
    directory = tempfile.mkdtemp(prefix="bottest_")
    db.DB_FILE = Path(directory) / "test.db"
    db.init_db()
    db.yf = _FakeYF(100.0)
    db.get_or_create_user(TELEGRAM_CHAT, "telegramtester")
    db.save_profile(TELEGRAM_CHAT, trade_size_eur=100.0)


def _telegram_accept_update(ticker):
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.data = f"accept:{ticker}"
    query.message.text = f"Signal {ticker}"
    update = MagicMock()
    update.callback_query = query
    update.effective_chat.id = TELEGRAM_CHAT
    return update, query


def _order_count(idempotency_key):
    with db._connect() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM orders WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()[0]


def test_web_double_post_submits_exactly_one_order(monkeypatch):
    _fresh_web()
    db.set_broker_exec(WEB_CHAT, True)
    db.add_pending(WEB_CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0,
                              "leverage": 1.0, "strength": 70.0}, 1)
    submissions = []

    class PaperClient:
        _paper = True

    monkeypatch.setattr(webapp, "_alpaca_ready", lambda user: True)
    monkeypatch.setattr(webapp, "_alpaca_client", lambda user: PaperClient())
    monkeypatch.setattr(webapp, "_attach_demo_option", lambda user, ticker: None)
    monkeypatch.setattr(webapp.broker, "account_summary",
                        lambda client=None: {"ok": True, "buying_power": 1000.0})
    monkeypatch.setattr(webapp.risk_context, "account_context", lambda client, user_id: {})
    monkeypatch.setattr(webapp.risk_context, "quote_context", lambda ticker: {})
    monkeypatch.setattr(
        webapp.sizing, "plan_order",
        lambda entry, budget, leverage, option_selector=None, extended=False, **kwargs:
        {"kind": "stock", "qty": 1},
    )
    monkeypatch.setattr(
        webapp.broker, "submit_buy",
        lambda symbol, **kwargs: submissions.append((symbol, kwargs))
        or {"ok": True, "id": "ord-web-double", "detail": "NVDA ×1"},
    )
    monkeypatch.setattr(
        webapp.broker, "get_order_status",
        lambda order_id, client=None: {"ok": True, "status": "filled",
                                       "filled_qty": 1.0, "filled_avg_price": 100.0},
    )

    client = _web_client()
    headers = {"X-Requested-With": "fetch"}
    first = client.post("/app/accept", data={"ticker": "NVDA"}, headers=headers)
    trade = db.get_trade(WEB_CHAT, "NVDA")
    key = f"web:{WEB_CHAT}:{trade['id']}:accept"
    first_order = db.get_order_by_idempotency_key(key)
    second = client.post("/app/accept", data={"ticker": "NVDA"}, headers=headers)

    # Doppelklick-Beweis: der erste Accept kauft und konsumiert das Pending-Signal; der zweite
    # POST erzeugt KEINE zweite Broker-Order, sondern wird sicher als "nicht mehr verfügbar"
    # abgewiesen (Idempotenz greift bereits am Service-Layer; der OMS-Idempotency-Key ist die
    # zweite Verteidigungslinie). Entscheidend: genau EINE Broker-Order, genau EINE OMS-Order.
    assert first.json()["ok"] is True
    assert second.json()["ok"] is False          # kein Duplikat-Kauf, saubere Abweisung
    assert len(submissions) == 1
    assert first_order is not None
    assert db.get_order_by_idempotency_key(key)["id"] == first_order["id"]
    assert _order_count(key) == 1


def test_telegram_double_callback_submits_exactly_one_order(monkeypatch):
    _fresh_telegram()
    db.set_broker_exec(TELEGRAM_CHAT, True)
    db.add_pending(TELEGRAM_CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0,
                                  "leverage": 1.0, "strength": 70.0}, 1)
    submissions = []

    class PaperClient:
        _paper = True

    monkeypatch.setattr(bot, "ALPACA_ENABLED", True)
    monkeypatch.setattr(bot, "_alpaca_ready", lambda user: True)
    monkeypatch.setattr(bot, "_alpaca_client", lambda user: PaperClient())
    monkeypatch.setattr(bot, "_attach_demo_option", lambda user, trade: None)
    monkeypatch.setattr(bot, "_us_market_open", lambda extended=False: True)
    monkeypatch.setattr(bot.broker, "account_summary",
                        lambda client=None: {"ok": True, "buying_power": 1000.0})
    monkeypatch.setattr(bot.risk_context, "account_context", lambda client, user_id: {})
    monkeypatch.setattr(bot.risk_context, "quote_context", lambda ticker: {})
    monkeypatch.setattr(
        bot.sizing, "plan_order",
        lambda entry, budget, leverage, option_selector=None, extended=False, **kwargs:
        {"kind": "shares", "qty": 1, "notional": None},
    )
    monkeypatch.setattr(
        bot.broker, "submit_buy",
        lambda symbol, **kwargs: submissions.append((symbol, kwargs))
        or {"ok": True, "id": "ord-telegram-double", "status": "filled", "detail": "filled"},
    )
    monkeypatch.setattr(
        bot.broker, "get_order_status",
        lambda order_id, client=None: {"ok": True, "status": "filled",
                                       "filled_qty": 1.0, "filled_avg_price": 100.0},
    )
    monkeypatch.setattr(
        bot, "_await_fill",
        AsyncMock(return_value={"ok": True, "status": "filled",
                                "filled_qty": 1.0, "filled_avg_price": 100.0}),
    )
    monkeypatch.setattr(bot, "_reconcile_and_alert", AsyncMock())

    context = MagicMock()
    context.bot = AsyncMock()
    first_update, first_query = _telegram_accept_update("AAPL")
    asyncio.run(bot.button_handler(first_update, context))
    trade = db.get_trade(TELEGRAM_CHAT, "AAPL")
    key = f"telegram:{TELEGRAM_CHAT}:{trade['id']}:accept"
    first_order = db.get_order_by_idempotency_key(key)
    second_update, second_query = _telegram_accept_update("AAPL")
    asyncio.run(bot.button_handler(second_update, context))

    # Entscheidend: genau EINE Broker-Order, genau EINE OMS-Order, genau EINE aktive Position —
    # der zweite Callback erzeugt kein Duplikat. Der Callback wird beantwortet (>=1×); wie oft
    # genau (Guard + Abschluss) ist ein UI-Detail, kein Sicherheitskriterium.
    assert len(submissions) == 1
    assert first_order is not None
    assert db.get_order_by_idempotency_key(key)["id"] == first_order["id"]
    assert _order_count(key) == 1
    assert len(db.get_active_trades(TELEGRAM_CHAT)) == 1
    assert second_query.answer.await_count >= 1
