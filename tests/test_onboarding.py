"""
Tests für die sicherheitskritischen Onboarding-/Broker-Dialoge:
  - onboarding.py: geführtes /start-Setup (Demo-Größe + optionale Broker-Keys)
  - broker/setup.py: /connectalpaca, /disconnectalpaca

Schwerpunkt (Sicherheit):
  • Die Nachricht mit dem Geheimnis (API-Key/-Secret) wird aus dem Chat gelöscht.
  • Zugangsdaten werden NUR verschlüsselt gespeichert (roh kein Klartext in der DB).
  • Vor dem Speichern prüft /connectalpaca die Keys per Health-Check — bei Fehlschlag
    wird NICHTS gespeichert.

Lauf:  python -m tests.test_onboarding   oder   pytest tests/test_onboarding.py
Alle Tests laufen offline (Telegram + Alpaca werden gemockt).
"""

import sys
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import ConversationHandler

from stockbot.core import db
from stockbot.tgbot import onboarding
from stockbot.broker import setup

CHAT = 9090


def fresh_db():
    d = tempfile.mkdtemp(prefix="onbtest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()


def _fake_update(text="", user_id=CHAT, username="tester"):
    """Minimaler Update-Stub mit asynchronen reply_text/delete."""
    upd = MagicMock()
    upd.effective_user.id = user_id
    upd.effective_user.username = username
    upd.message.text = text
    upd.message.reply_text = AsyncMock()
    upd.message.delete = AsyncMock()
    return upd


def _ctx():
    ctx = MagicMock()
    ctx.user_data = {}
    return ctx


def _raw_creds(user_id):
    with db._connect() as conn:
        return conn.execute(
            "SELECT broker_api_key, broker_api_secret FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()


# ── onboarding.py: geführtes Setup ───────────────────────────────────────────

def test_onboarding_rejects_nonpositive_trade_size():
    fresh_db()
    db.get_or_create_user(CHAT)
    ctx = _ctx()
    state = asyncio.run(onboarding.ask_trade_size(_fake_update("-5"), ctx))
    assert state == onboarding.ASK_TRADE_SIZE          # bleibt im selben Schritt
    assert "trade_size_eur" not in ctx.user_data


def test_onboarding_without_broker_finishes_demo_only():
    fresh_db()
    db.get_or_create_user(CHAT)
    ctx = _ctx()
    ctx.user_data["trade_size_eur"] = 50.0
    state = asyncio.run(onboarding.ask_broker_yesno(_fake_update("nein"), ctx))
    assert state == ConversationHandler.END
    u = db.get_user(CHAT)
    assert u["onboarding_state"] == "complete"
    assert u["broker_platform"] is None
    assert db.get_decrypted_credentials(CHAT) is None


def test_onboarding_with_broker_encrypts_and_deletes_secret_messages():
    fresh_db()
    db.get_or_create_user(CHAT, "tester")
    ctx = _ctx()

    # 1/3 Demo-Größe
    state = asyncio.run(onboarding.ask_trade_size(_fake_update("25"), ctx))
    assert state == onboarding.ASK_BROKER_YESNO
    assert ctx.user_data["trade_size_eur"] == 25.0

    # 2/3 "ja" → Plattform → "alpaca"
    assert asyncio.run(onboarding.ask_broker_yesno(_fake_update("ja"), ctx)) == onboarding.ASK_BROKER_PLATFORM
    assert asyncio.run(onboarding.ask_broker_platform(_fake_update("alpaca"), ctx)) == onboarding.ASK_BROKER_KEY

    # 3/3 API-Key → Nachricht wird gelöscht
    uk = _fake_update("MY_API_KEY")
    assert asyncio.run(onboarding.ask_broker_key(uk, ctx)) == onboarding.ASK_BROKER_SECRET
    uk.message.delete.assert_awaited_once()
    assert ctx.user_data["broker_api_key"] == "MY_API_KEY"

    # API-Secret → Nachricht gelöscht, Onboarding abgeschlossen
    us = _fake_update("MY_API_SECRET")
    assert asyncio.run(onboarding.ask_broker_secret(us, ctx)) == ConversationHandler.END
    us.message.delete.assert_awaited_once()

    # entschlüsselt korrekt zurück …
    assert db.get_decrypted_credentials(CHAT) == ("MY_API_KEY", "MY_API_SECRET")
    assert db.get_user(CHAT)["broker_platform"] == "alpaca"
    # … aber roh in der DB NICHT im Klartext
    row = _raw_creds(CHAT)
    assert b"MY_API_KEY" not in bytes(row["broker_api_key"])
    assert b"MY_API_SECRET" not in bytes(row["broker_api_secret"])
    # user_data wurde am Ende geleert (kein Geheimnis im Speicher belassen)
    assert ctx.user_data == {}


# ── broker/setup.py: /connectalpaca mit Health-Check-Gate ────────────────────

class _FakeBroker:
    """Ersetzt das Broker-Client-Modul: Health-Check je nach `ok` erfolgreich/fehlerhaft."""
    def __init__(self, ok): self._ok = ok
    def make_client(self, key, secret, paper=True): return ("client", key, secret)
    def health_check(self, client=None):
        if self._ok:
            return {"ok": True, "paper": True, "status": "ACTIVE", "buying_power": 1000.0}
        return {"ok": False, "detail": "ungültige Zugangsdaten"}


def _complete_user():
    db.get_or_create_user(CHAT)
    db.save_profile(CHAT, trade_size_eur=25.0)         # onboarding_state -> complete


def test_connect_start_requires_registration():
    fresh_db()
    db.get_or_create_user(CHAT)                        # in_progress, NICHT complete
    u = _fake_update("")
    assert asyncio.run(setup.connect_start(u, _ctx())) == ConversationHandler.END
    u.message.reply_text.assert_awaited()


def test_connect_alpaca_stores_encrypted_on_successful_healthcheck():
    fresh_db()
    _complete_user()
    ctx = _ctx()

    uk = _fake_update("AKEY")
    assert asyncio.run(setup.ask_key(uk, ctx)) == setup.ASK_ALPACA_SECRET
    uk.message.delete.assert_awaited_once()
    assert ctx.user_data["alpaca_key"] == "AKEY"

    orig = setup.broker
    setup.broker = _FakeBroker(ok=True)
    us = _fake_update("ASECRET")
    try:
        assert asyncio.run(setup.ask_secret(us, ctx)) == ConversationHandler.END
    finally:
        setup.broker = orig

    us.message.delete.assert_awaited_once()
    assert db.get_decrypted_credentials(CHAT) == ("AKEY", "ASECRET")
    row = _raw_creds(CHAT)
    assert b"ASECRET" not in bytes(row["broker_api_secret"])   # verschlüsselt, kein Klartext


def test_connect_alpaca_rejects_bad_creds_and_stores_nothing():
    fresh_db()
    _complete_user()
    ctx = _ctx()
    asyncio.run(setup.ask_key(_fake_update("AKEY"), ctx))

    orig = setup.broker
    setup.broker = _FakeBroker(ok=False)
    us = _fake_update("ASECRET")
    try:
        assert asyncio.run(setup.ask_secret(us, ctx)) == ConversationHandler.END
    finally:
        setup.broker = orig

    us.message.delete.assert_awaited_once()             # Geheimnis trotzdem gelöscht
    assert db.get_decrypted_credentials(CHAT) is None    # aber NICHTS gespeichert
    assert db.has_alpaca_credentials(CHAT) is False


def test_disconnect_alpaca_clears_credentials():
    fresh_db()
    _complete_user()
    db.set_alpaca_credentials(CHAT, "AK", "SK")
    assert db.has_alpaca_credentials(CHAT) is True
    asyncio.run(setup.disconnect(_fake_update(""), _ctx()))
    assert db.has_alpaca_credentials(CHAT) is False
    assert db.get_decrypted_credentials(CHAT) is None


# ── Runner (ohne pytest nutzbar) ─────────────────────────────────────────────

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
