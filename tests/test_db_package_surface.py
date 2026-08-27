"""Sichert die Aufteilung von `stockbot/core/db.py` in das Paket `stockbot/core/db/`.

`db` hat quer durch das Projekt sehr viele Aufrufer, die alle `from stockbot.core import db`
schreiben und dann `db.irgendwas(...)` rufen. Ein bei der Aufteilung vergessener Re-Export
fällt deshalb nicht beim Import auf, sondern erst zur Laufzeit — im schlechtesten Fall im
Handelspfad. Diese Tests machen daraus einen roten Test:

* `test_oberflaeche_von_vor_der_aufteilung_...` friert die Namen ein, die `db.py` vor der
  Aufteilung hatte.
* `test_jedes_fachmodul_...` prüft dieselbe Eigenschaft für die Zukunft: kein Fachmodul
  darf einen Namen definieren, den das Paket nicht re-exportiert.
* Die Naht-Tests sichern die Eigenschaft, auf der die Aufteilung ruht: Tests ersetzen
  Namen wie `db.DB_FILE` oder `db._today` auf dem **Paket**, und das muss die Fachmodule
  weiterhin erreichen.
"""

import ast
import pkgutil
from pathlib import Path

from stockbot.core import db

# Die 167 Top-Level-Namen, die `stockbot/core/db.py` vor der Aufteilung exportiert hat.
# Bewusst inklusive der unterstrichenen: `_trade_to_dict`, `_hash_token`,
# `_polymarket_timestamp`, `_migrate_leverage_values` und `SCHEMA_SQL` werden von Tests und
# von `tools/` benutzt, `_today`/`_utc_timestamp`/`yf`/`DB_FILE` sind Test-Nähte.
OBERFLAECHE_VOR_DER_AUFTEILUNG = (
    "DB_FILE", "SCHEMA_SQL", "_EXPECTED_POSTGRES_TABLES", "_STRATEGY_VERSIONS_BOOTSTRAPPED",
    "_STRATEGY_VERSION_CACHE", "_SignalQuoteSource", "_as_audit_event", "_audit_timestamp",
    "_check_postgres_schema_readiness", "_connect", "_current_code_commit", "_database",
    "_fernet", "_hash_token", "_is_token_hash", "_kill_switch_timestamp",
    "_latest_strategy_version_id", "_log_event", "_migrate", "_migrate_leverage_values",
    "_mutate_user_text", "_parse_regions", "_parse_strategies", "_parse_watchlist",
    "_polymarket_timestamp", "_strategy_config_to_dict", "_strategy_content_hash",
    "_terminate_pending", "_today", "_trade_to_dict", "_update_user", "_user_to_dict",
    "_utc_timestamp", "_with_strategy_version", "activate_kill_switch", "activate_trade",
    "add_notification", "add_pending", "add_tick", "add_watchlist_tickers",
    "adopt_active_trade", "all_audit_events", "append_audit_event", "audit_events_for_entity",
    "burn_in_order_stats", "clear_alpaca_credentials", "close_all", "create_oms_order",
    "create_session", "deactivate_kill_switch", "decrypt", "delete_expired_sessions",
    "delete_session", "delete_user_sessions", "disconnect_broker_oauth_connection", "encrypt",
    "enqueue_outbox_event", "ensure_strategy_versions_published", "expire_stale_pending",
    "expire_trade", "fetch_due_outbox_events", "get_active_kill_switches",
    "get_active_protective_orders", "get_active_trades", "get_all_trades",
    "get_all_trades_between", "get_broker_closing_trades", "get_broker_oauth_connection",
    "get_broker_pending_trades", "get_closed_trade_results_since", "get_closed_trades",
    "get_decrypted_credentials", "get_events_by_trade", "get_history", "get_notifications",
    "get_oms_order", "get_oms_order_events", "get_oms_trade_intent", "get_open_oms_orders",
    "get_or_create_dashboard_token", "get_or_create_user", "get_order_by_idempotency_key",
    "get_pending_trades", "get_post_trade_risk_rows", "get_realized_pnl_today",
    "get_risk_profile", "get_shadow_snapshots", "get_strategy_config", "get_strategy_version",
    "get_today_ticks", "get_trade", "get_trade_by_id", "get_trade_events",
    "get_trade_events_between", "get_user", "get_user_by_token", "has_alpaca_credentials",
    "has_open_position", "has_trade_today", "heal_absurd_closed_pnl", "init_db",
    "issue_callback_token", "list_active_users", "list_polymarket_markets",
    "list_polymarket_snapshots", "list_raw_data_archive_entries", "list_strategy_configs",
    "log", "mark_broker_close_failed", "mark_broker_closing", "mark_broker_failed",
    "mark_broker_filled", "mark_broker_pending", "mark_notifications_read", "mark_outbox_dead",
    "mark_outbox_delivered", "mark_outbox_retry", "merge_active_trade_signal",
    "outbox_backlog_count", "polymarket_snapshot_history", "publish_strategy_version",
    "purge_expired_callback_tokens", "record_oms_order_event", "record_polymarket_snapshot",
    "record_protective_order", "record_raw_data_archive_entry", "record_shadow_snapshot",
    "reject_trade", "remove_watchlist_ticker", "reset_user_trades", "resolve_callback_token",
    "resolve_strategy_version_id", "revoke_broker_oauth_connection", "rotate_dashboard_token",
    "save_profile", "save_risk_profile", "search_strategy_configs", "set_active_entry",
    "set_alpaca_credentials", "set_asset_pref", "set_auto_accept", "set_auto_universe",
    "set_broker_exec", "set_eod_close", "set_leverage", "set_llm_rank", "set_market_region",
    "set_notify_channel", "set_signal_window", "set_sl_tp_mode", "set_strategy", "set_top_n",
    "set_trade_leverage", "set_trade_size", "set_user_active", "store_broker_oauth_connection",
    "today_utc", "today_utc_date", "toggle_region", "toggle_strategy", "transition_oms_order",
    "unread_count", "update_high_water", "upsert_polymarket_market", "upsert_strategy_config",
    "user_id_for_session", "yf",
)


def _fachmodule() -> list[str]:
    return sorted(m.name for m in pkgutil.iter_modules(db.__path__))


def _top_level_namen(modulname: str) -> list[str]:
    quelle = Path(db.__path__[0]) / f"{modulname}.py"
    namen = []
    for node in ast.parse(quelle.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            namen.append(node.name)
        elif isinstance(node, ast.Assign):
            namen += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            namen.append(node.target.id)
    return namen


def test_oberflaeche_von_vor_der_aufteilung_ist_vollstaendig_erreichbar():
    fehlend = [name for name in OBERFLAECHE_VOR_DER_AUFTEILUNG if not hasattr(db, name)]
    assert fehlend == [], f"nicht mehr über `db.` erreichbar: {fehlend}"


def test_jedes_fachmodul_ist_vollstaendig_re_exportiert():
    """Fängt den umgekehrten Fehler: ein neuer Name im Fachmodul, im Re-Export vergessen."""
    fehlend = [f"{modul}.{name}"
               for modul in _fachmodule()
               for name in _top_level_namen(modul)
               if not hasattr(db, name)]
    assert fehlend == [], (
        "in stockbot/core/db/__init__.py nicht re-exportiert: " + ", ".join(fehlend))


def test_die_paketweite_naht_erreicht_die_fachmodule(monkeypatch, tmp_path):
    """Tests ersetzen Nähte auf dem Paket — das muss in den Fachmodulen ankommen.

    Ohne diese Eigenschaft wäre die Aufteilung nicht zulässig: rund 50 Teststellen und
    `tools/seed_design_data.py` sprechen `db.DB_FILE`, `db._today` und `db.yf` an.
    """
    monkeypatch.setattr(db, "_today", lambda: "1999-12-31")
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "naht.db")
    monkeypatch.setattr(db, "yf", "ersetzt")

    assert db.trades.db._today() == "1999-12-31"
    assert db.trade_queries.db._today() == "1999-12-31"
    assert db.schema.db.DB_FILE == tmp_path / "naht.db"
    assert db.trades.db.yf == "ersetzt"


def test_nur_init_db_ruft_connect_direkt_auf():
    """`_connect()` aufzurufen öffnet immer SQLite und ignoriert `DB_BACKEND`.

    Laufzeitfunktionen gehen deshalb über `db._database().transaction()`; `_connect` darf
    nur als **Fabrik** an `db_backend.get_database(...)` übergeben (nicht gerufen) werden.
    Einzige Ausnahme ist der SQLite-Zweig von `init_db()`, der das Schema anlegt — schon
    vor der Aufteilung. Diese Liste darf wachsen, aber nie unbemerkt.
    """
    aufrufer = set()
    for modul in _fachmodule():
        quelle = Path(db.__path__[0]) / f"{modul}.py"
        baum = ast.parse(quelle.read_text(encoding="utf-8"))
        for funktion in ast.walk(baum):
            if not isinstance(funktion, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(funktion):
                if not isinstance(node, ast.Call):
                    continue
                ziel = node.func
                trifft = ((isinstance(ziel, ast.Attribute) and ziel.attr == "_connect")
                          or (isinstance(ziel, ast.Name) and ziel.id == "_connect"))
                if trifft:
                    aufrufer.add(f"{modul}.{funktion.name}")
    assert aufrufer == {"schema.init_db"}, f"rohes _connect() ausserhalb init_db: {aufrufer}"
