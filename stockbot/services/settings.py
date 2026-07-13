"""
Einstellungs-Aktionen als framework-neutraler Service.

`apply_setting` bündelt den Dispatch, der bisher im Telegram-`button_handler` als großes if/elif
verdrahtet war. Eingabe sind die Aktions-/Wert-Strings der Buttons (bzw. künftig Web-Formulare);
die eigentliche Persistenz/Validierung (Clamping etc.) liegt weiterhin in `db`/`strategies`.
"""

import logging

from stockbot.core import db
from stockbot.market import strategies
from stockbot.config import UNIVERSES, SL_TP_MODES

log = logging.getLogger(__name__)

# Alle von der Einstellungs-Ansicht ausgelösten Aktionen (für die Weiche im Aufrufer).
SETTING_ACTIONS = frozenset({
    "set_region", "set_size", "set_count", "set_mode", "set_lev",
    "set_auto", "set_uni", "set_strat", "set_llm", "set_eod", "set_broker", "set_window",
})


def toggle_strategy_selection(user_id: int, key: str) -> bool:
    """Schaltet eine zulässige Nutzerstrategie um.

    Research-only-Strategien dürfen nicht neu hinzukommen. Ist ein solcher Key bereits
    gespeichert, bleibt er gültig und kann weiterhin bewusst entfernt werden. Damit werden
    Bestandsnutzer weder migriert noch im laufenden Signalpfad unterbrochen.
    """
    strategy = strategies.REGISTRY.get(key)
    user = db.get_user(user_id)
    if strategy is None or user is None:
        return False
    active = user.get("strategies") or []
    if key not in active and not strategies.is_selectable_for_new_users(key):
        log.warning("[%s] Neue Research-only-Strategie abgelehnt: %s", user_id, key)
        return False
    db.toggle_strategy(user_id, key)
    return True


def apply_setting(user_id: int, action: str, value: str, *, alpaca_ready: bool = False) -> dict | None:
    """Wendet eine Einstellungs-Aktion an und gibt den aktualisierten Nutzer zurück (oder None).

    `value` ist der Roh-String aus dem Button/Formular. Ungültige Werte werden still ignoriert
    (Verhalten wie bisher). `alpaca_ready` gated den Broker-Ausführungs-Schalter.
    """
    if action == "set_region" and value in UNIVERSES:
        db.toggle_region(user_id, value)
    elif action == "set_size":
        try:
            db.set_trade_size(user_id, float(value))
        except ValueError:
            pass
    elif action == "set_count":
        try:
            db.set_top_n(user_id, int(value))
        except ValueError:
            pass
    elif action == "set_mode" and value in SL_TP_MODES:
        db.set_sl_tp_mode(user_id, value)
    elif action == "set_lev":
        try:
            db.set_leverage(user_id, float(value))
        except ValueError:
            pass
    elif action == "set_auto":
        db.set_auto_accept(user_id, value == "1")
    elif action == "set_uni":
        db.set_auto_universe(user_id, value == "1")
    elif action == "set_strat":
        toggle_strategy_selection(user_id, value)
    elif action == "set_llm":
        db.set_llm_rank(user_id, value == "1")
    elif action == "set_eod":
        db.set_eod_close(user_id, value == "1")
    elif action == "set_window":
        db.set_signal_window(user_id, value == "1")
    elif action == "set_broker" and alpaca_ready:
        db.set_broker_exec(user_id, value == "1")

    log.info(f"[{user_id}] Einstellung geändert: {action}={value}")
    return db.get_user(user_id)
