"""Der globale Alpaca-Key ist Betreiber-Datenzugang, kein Handelskonto für fremde Nutzer.

Hintergrund (2026-07-21): Für den Signal-Scan mussten globale Marktdaten-Credentials auf den
Server. Damit wurde `config.ALPACA_ENABLED` wahr — und über diesen Schalter hätte **jeder**
Nutzer ohne eigene Anbindung „echte Broker-Order" aktivieren und über das Konto des Betreibers
handeln können. Diese Tests nageln die Trennung fest:

* Order-Ausführung  → ausschließlich eigene, verschlüsselt gespeicherte Nutzer-Keys.
* Marktdaten/Optionen → globaler Rückfall erlaubt (Kurse sind nutzerunabhängig).
"""
from __future__ import annotations

from stockbot.core import db          # noqa: F401  (Import-Reihenfolge: vor webapp)
from stockbot.web import dashboard as _dashboard   # noqa: F401  (Zirkularimport vermeiden)
from stockbot.tgbot import bot as tgbot
from stockbot.web import webapp

USER_OHNE_KEYS = {"user_id": 1, "broker_exec": 1, "broker_platform": None}
USER_MIT_KEYS = {"user_id": 2, "broker_exec": 1, "broker_platform": "alpaca"}


def _force_global_keys(monkeypatch):
    """Globale Marktdaten-Keys sind gesetzt — der Normalzustand in Produktion."""
    monkeypatch.setattr(webapp.config, "ALPACA_ENABLED", True, raising=False)
    monkeypatch.setattr(webapp.config, "ALPACA_API_KEY", "OPERATOR-KEY", raising=False)
    monkeypatch.setattr(webapp.config, "ALPACA_API_SECRET", "OPERATOR-SECRET", raising=False)
    monkeypatch.setattr(tgbot, "ALPACA_ENABLED", True, raising=False)


def test_web_user_without_own_credentials_gets_no_broker_client(monkeypatch):
    _force_global_keys(monkeypatch)
    monkeypatch.setattr(webapp.broker, "_get_client",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("globaler Client darf nicht für Orders dienen")))

    assert webapp._alpaca_ready(USER_OHNE_KEYS) is False
    assert webapp._alpaca_client(USER_OHNE_KEYS) is None


def test_telegram_user_without_own_credentials_gets_no_broker_client(monkeypatch):
    _force_global_keys(monkeypatch)
    monkeypatch.setattr(tgbot.broker, "_get_client",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("globaler Client darf nicht für Orders dienen")))

    assert tgbot._alpaca_ready(USER_OHNE_KEYS) is False
    assert tgbot._alpaca_client(USER_OHNE_KEYS) is None


def test_user_with_own_credentials_still_gets_a_client(monkeypatch):
    _force_global_keys(monkeypatch)
    monkeypatch.setattr(webapp.db, "get_decrypted_credentials", lambda uid: ("USER-KEY", "USER-SEC"))
    built = {}

    def _make_client(key, secret, paper=True):
        built["key"], built["secret"] = key, secret
        return object()

    monkeypatch.setattr(webapp.broker, "make_client", _make_client)

    assert webapp._alpaca_ready(USER_MIT_KEYS) is True
    assert webapp._alpaca_client(USER_MIT_KEYS) is not None
    assert built == {"key": "USER-KEY", "secret": "USER-SEC"}    # nie der Betreiber-Key


def test_market_data_keys_may_still_fall_back_to_the_operator_key(monkeypatch):
    """Gegenprobe: Kursdaten sind nutzerunabhängig, hier ist der globale Key richtig."""
    _force_global_keys(monkeypatch)
    monkeypatch.setattr(webapp.db, "get_decrypted_credentials", lambda uid: None)

    assert webapp._alpaca_keys(USER_OHNE_KEYS) == ("OPERATOR-KEY", "OPERATOR-SECRET")
