"""
Reine Telegram-Nachrichtenformatierung für `stockbot/tgbot/bot.py`.

Ausgelagert aus bot.py (agent/REPO-B2-NAV), um die Hauptdatei etwas zu entlasten.
Enthält NUR Funktionen ohne Seiteneffekte: kein `db.`-Zugriff, kein `context`,
kein `await` — reine Eingabe-zu-Text-Transformationen. bot.py importiert sie
unter ihrem alten (privaten) Namen zurück, damit Aufrufstellen und bestehende
Tests unverändert bleiben.

Nicht hierher gehören `_signal_card`/`_trade_card`/`_settings_view` aus bot.py —
sie SEHEN rein aus, lösen aber Seiteneffekte aus (Callback-Token-Ausgabe in die
DB über `_secure_cb`→`callback_security.issue`, bzw. bei `_trade_card` zusätzlich
einen Live-Kursabruf über `_unrealized_pnl`) oder hängen an bot.py-lokalen
Nutzerprofil-Helfern (`_user_regions`, `_auto_uni`, `_user_strategies`,
`_llm_enabled`, `_alpaca_ready`), die weit über die Nachrichtenformatierung
hinaus im ganzen Modul gebraucht werden. Sie bleiben bewusst in bot.py.
"""

from __future__ import annotations

from stockbot.market import strategies


def format_strength(v) -> str:
    """Kompatiblen Strategie-Rohscore formatieren; '—' wenn unbekannt."""
    return f"{v:.0f}" if v is not None else "—"


def strategy_label(signal_or_key) -> str:
    """Anzeigename einer Strategie — akzeptiert Strategie-Key ODER Signal-/Trade-Dict."""
    key = signal_or_key if isinstance(signal_or_key, str) else (signal_or_key or {}).get("strategy")
    key = key or strategies.DEFAULT_STRATEGY
    return strategies.get(key).label
