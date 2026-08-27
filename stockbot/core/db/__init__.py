"""
Laufzeit-Persistenz für Multi-User-Betrieb (SQLite/PostgreSQL).

Trade-Lifecycle-Übergänge setzen zunächst READ COMMITTED voraus und schützen den Zustand
mit Compare-and-set-Updates (statusbewachtes ``UPDATE`` + ``rowcount``). Statusänderung und
Trade-Event teilen stets dieselbe Seam-Transaktion; Row Locks werden nicht eingesetzt, wo
CAS den einzigen Gewinner bereits eindeutig bestimmt.

SQLite-Persistenz für Multi-User-Betrieb
Ersetzt tracker.py — speichert Nutzerprofile (inkl. verschlüsselter
Broker-Zugangsdaten) und Demo-Trades, jeweils pro user_id (== Telegram chat_id).
"""

import sqlite3
import json
import hashlib
import logging
import secrets
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

from stockbot import config
from stockbot.config import ENCRYPTION_KEY, MAX_LEVERAGE
from stockbot.core import db_backend
from stockbot.paths import DATA_DIR

log = logging.getLogger(__name__)
DB_FILE = DATA_DIR / "bot.db"


class _SignalQuoteSource:
    """Kurschnittstelle für die Trade-Aktivierung (Einstiegskurs). Liefert `Ticker(t).fast_info.
    last_price` aus dem PRODUKTIONS-Signalprovider (Alpaca, nie yfinance — Leitplanke W3.2) und
    bewahrt dabei die bestehende Test-Naht ``db.yf`` (Tests ersetzen ``db.yf`` bzw. patchen
    ``db.yf.Ticker`` weiterhin, ohne dass yfinance im Prod-Pfad landet)."""

    class _FastInfo:
        def __init__(self, price):
            self.last_price = price

    class _Ticker:
        def __init__(self, price):
            self.fast_info = _SignalQuoteSource._FastInfo(price)

    def Ticker(self, ticker):
        from stockbot.market import provider_factory
        price = provider_factory.get_signal_provider().get_quote(ticker).price
        return self._Ticker(float(price))


# ``yf`` bleibt der Name der Kursnaht (Rückwärtskompatibilität für Tests), zeigt aber NICHT mehr
# auf das yfinance-Modul, sondern auf den Alpaca-gestützten Signalprovider (Leitplanke W3.2).
yf = _SignalQuoteSource()

_fernet = Fernet(ENCRYPTION_KEY.encode())

@contextmanager
def _connect():
    """Öffnet eine SQLite-Verbindung mit Spaltenzugriff per Name; committet & schließt automatisch."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def today_utc_date() -> date:
    """Der heutige Handelstag als `date` — **UTC**, nicht Server-Lokalzeit.

    Die eine Wahrheit für „welcher Tag ist heute" in der gesamten Anwendung (Backtests
    haben mit `backtest/clock.py` bewusst einen eigenen Zeitbegriff). Alle Zeitstempel in
    der DB folgen dem naiven UTC-Vertrag (`_utc_timestamp()`), `trade_date` ebenso.
    `date.today()` folgt dagegen der Server-Zeitzone: auf einer Maschine mit Offset
    (z. B. CEST = UTC+2) liegt der lokale Tag zwischen Mitternacht und dem Offset einen
    Tag vor/nach dem UTC-Tag — wer damit gegen DB-Werte vergleicht, greift ins Leere.
    Produktion (VPS, `Etc/UTC`) verhält sich unverändert.
    """
    return datetime.now(timezone.utc).date()


def today_utc() -> str:
    """Derselbe Handelstag als ISO-String 'YYYY-MM-DD' (Format der `trade_date`-Spalte)."""
    return str(today_utc_date())


def _today() -> str:
    """DB-interner Name für `today_utc()` (stempelt die `trade_date`-Spalte)."""
    return today_utc()


# ── Verschlüsselung ─────────────────────────────────────────────────────────

def encrypt(plaintext: str) -> bytes:
    """Verschlüsselt einen String zur Speicherung (z. B. Broker-API-Key/-Secret)."""
    return _fernet.encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Entschlüsselt aus der DB gelesene Bytes zurück zum ursprünglichen String."""
    return _fernet.decrypt(bytes(ciphertext)).decode("utf-8")


def _utc_timestamp(moment: datetime | None = None) -> str:
    """SQLite-compatible UTC TEXT timestamp (seconds precision)."""
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")


def _database():
    return db_backend.get_database(config.DB_BACKEND, _connect)


from . import strategies   # init_db setzt den Strategieversions-Cache zurück

# Strategie-Konfigurationen und -Versionen.
# `_STRATEGY_VERSIONS_BOOTSTRAPPED` ist ein modulinterner Cache-Schalter; maßgeblich ist
# immer `strategies._STRATEGY_VERSIONS_BOOTSTRAPPED`, der Wert hier ist nur eine Kopie.
from .strategies import (                                                      # noqa: E402
    _strategy_config_to_dict, list_strategy_configs, get_strategy_config,
    upsert_strategy_config, search_strategy_configs, _STRATEGY_VERSION_CACHE,
    _STRATEGY_VERSIONS_BOOTSTRAPPED, _current_code_commit, _strategy_content_hash,
    publish_strategy_version, get_strategy_version, _latest_strategy_version_id,
    ensure_strategy_versions_published, resolve_strategy_version_id, _with_strategy_version,
)

# Rohdatenarchiv, Shadow-Snapshots, Polymarket
from .research import (                                                        # noqa: E402
    record_raw_data_archive_entry, list_raw_data_archive_entries, record_shadow_snapshot,
    get_shadow_snapshots, _polymarket_timestamp, upsert_polymarket_market,
    record_polymarket_snapshot, polymarket_snapshot_history, list_polymarket_markets,
    list_polymarket_snapshots,
)


# OMS-Orders, Order-Events, Schutzorders
from .orders import (                                                          # noqa: E402
    get_order_by_idempotency_key, get_oms_order, get_open_oms_orders,
    get_active_protective_orders, record_protective_order, get_oms_trade_intent,
    create_oms_order, transition_oms_order, record_oms_order_event, get_oms_order_events,
    burn_in_order_stats,
)

# Audit-Log, Kill-Switch, Risikoprofile
from .safety import (                                                          # noqa: E402
    _audit_timestamp, append_audit_event, _as_audit_event, audit_events_for_entity,
    all_audit_events, _kill_switch_timestamp, activate_kill_switch, deactivate_kill_switch,
    get_active_kill_switches, get_post_trade_risk_rows, get_risk_profile, save_risk_profile,
)

# Outbox und Callback-Tokens
from .messaging import (                                                       # noqa: E402
    enqueue_outbox_event, fetch_due_outbox_events, mark_outbox_delivered, mark_outbox_retry,
    mark_outbox_dead, outbox_backlog_count, issue_callback_token, resolve_callback_token,
    purge_expired_callback_tokens,
)


# Nutzer, Profil, Einstellungen, Zugangsdaten, Benachrichtigungen
from .users import (                                                           # noqa: E402
    _update_user, _mutate_user_text, _user_to_dict, _parse_strategies, _parse_watchlist,
    _parse_regions, get_or_create_user, get_user, save_profile, get_decrypted_credentials,
    store_broker_oauth_connection, get_broker_oauth_connection,
    disconnect_broker_oauth_connection, revoke_broker_oauth_connection, list_active_users,
    set_user_active, set_market_region, toggle_region, set_trade_size, set_top_n,
    set_sl_tp_mode, set_leverage, set_auto_accept, set_auto_universe, set_strategy,
    set_llm_rank, set_eod_close, set_signal_window, set_broker_exec, set_alpaca_credentials,
    clear_alpaca_credentials, has_alpaca_credentials, toggle_strategy, add_watchlist_tickers,
    remove_watchlist_ticker, set_notify_channel, set_asset_pref, add_notification,
    get_notifications, unread_count, mark_notifications_read,
)

# Dashboard-Token und Web-Sessions
from .sessions import (                                                        # noqa: E402
    get_or_create_dashboard_token, rotate_dashboard_token, get_user_by_token, _hash_token,
    _is_token_hash, create_session, user_id_for_session, delete_session,
    delete_user_sessions, delete_expired_sessions,
)


# Trade-Zustandsmaschine (Schreibpfad)
from .trades import (                                                          # noqa: E402
    _log_event, set_trade_leverage, merge_active_trade_signal, reset_user_trades,
    add_pending, activate_trade, set_active_entry, heal_absurd_closed_pnl,
    mark_broker_pending, mark_broker_filled, mark_broker_failed, mark_broker_closing,
    mark_broker_close_failed, adopt_active_trade, reject_trade, expire_trade,
    expire_stale_pending, _terminate_pending, close_all,
)

# Trade-Abfragen (Lesepfad)
from .trade_queries import (                                                   # noqa: E402
    get_closed_trade_results_since, _trade_to_dict, has_trade_today, has_open_position,
    get_active_trades, get_pending_trades, get_broker_pending_trades,
    get_broker_closing_trades, get_trade, get_trade_by_id, get_history, get_closed_trades,
    get_realized_pnl_today, get_all_trades, get_all_trades_between, get_trade_events,
    get_events_by_trade, get_trade_events_between,
)

# Intraday-Ticks und Höchstkurs
from .ticks import (                                                           # noqa: E402
    add_tick, update_high_water, get_today_ticks,
)


# Tabellen und Alt-Migrationen
from .schema import (                                                          # noqa: E402
    SCHEMA_SQL, init_db, _EXPECTED_POSTGRES_TABLES, _check_postgres_schema_readiness,
    _migrate, _migrate_leverage_values,
)
