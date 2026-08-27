"""Nutzer: Profil, Einstellungen, Broker-Zugangsdaten und Benachrichtigungen.

Ein Nutzer ist über seine Telegram-``chat_id`` identifiziert (== ``user_id``).
Broker-Zugangsdaten und OAuth-Token liegen ausschließlich verschlüsselt (Fernet) in der
Datenbank; Klartext existiert nur im Arbeitsspeicher des Aufrufers und nie in einem Log.
"""

import sqlite3
from stockbot import config
from stockbot.core import db_backend

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


def _update_user(statement: str, **params) -> int:
    params.setdefault("updated_at", db._utc_timestamp())
    with db._database().transaction() as transaction:
        return transaction.execute(statement, params)


def _mutate_user_text(user_id: int, column: str, mutate) -> str | None:
    """Optimistic CAS for user list fields, avoiding PostgreSQL lost updates.

    The guarded update is dialect-neutral and retries if another writer changed the
    value after our read. Five collisions indicate abnormal contention and are made
    visible instead of silently overwriting another update.
    """
    if column not in {"market_region", "strategy", "watchlist"}:
        raise ValueError(f"Unsupported user text column: {column}")
    for _ in range(5):
        with db._database().transaction() as transaction:
            row = transaction.one(
                f"SELECT {column} FROM users WHERE user_id = :user_id", {"user_id": user_id}
            )
            if row is None:
                return mutate(None)
            old_value = row[column]
            new_value = mutate(old_value)
            changed = transaction.execute(
                f"""UPDATE users SET {column} = :new_value, updated_at = :updated_at
                    WHERE user_id = :user_id AND {column} = :old_value""",
                {"new_value": new_value, "updated_at": db._utc_timestamp(),
                 "user_id": user_id, "old_value": old_value},
            )
            if changed == 1:
                return new_value
    raise RuntimeError(f"Concurrent user update did not converge: {column}")


# ── User-Profile ────────────────────────────────────────────────────────────

def _user_to_dict(row: sqlite3.Row) -> dict:
    return {
        "user_id":          row["user_id"],
        "username":         row["username"],
        "trade_size_eur":   row["trade_size_eur"],
        "broker_platform":  row["broker_platform"],
        "onboarding_state": row["onboarding_state"],
        "is_active":        bool(row["is_active"]),
        "market_region":    _parse_regions(row["market_region"])[0],   # primärer Bereich (Rückwärtskompat.)
        "market_regions":   _parse_regions(row["market_region"]),      # alle gewählten Körbe
        "top_n_signals":    row["top_n_signals"],
        "sl_tp_mode":       row["sl_tp_mode"],
        "leverage":         row["leverage"],
        "auto_accept":      bool(row["auto_accept"]),
        "auto_universe":    bool(row["auto_universe"]),
        "strategy":         row["strategy"],
        "strategies":       _parse_strategies(row["strategy"]),
        "llm_rank":         bool(row["llm_rank"]),
        "eod_close":        bool(row["eod_close"]),
        "broker_exec":      bool(row["broker_exec"]),
        "signal_window":    bool(row["signal_window"] if "signal_window" in row.keys() else 0),
        "watchlist":        _parse_watchlist(row["watchlist"] if "watchlist" in row.keys() else ""),
        "notify_channel":   (row["notify_channel"] if "notify_channel" in row.keys() else "both") or "both",
        "asset_pref":       (row["asset_pref"] if "asset_pref" in row.keys() else "stocks") or "stocks",
    }


def _parse_strategies(raw: str | None) -> list[str]:
    """Kommagetrennte Strategie-Liste aus der DB → Liste (mind. ein Eintrag)."""
    keys = [s.strip() for s in (raw or "").split(",") if s.strip()]
    return keys or ["standard"]


def _parse_watchlist(raw: str | None) -> list[str]:
    """Kommagetrennte persönliche Watchlist aus der DB → Liste (kann leer sein)."""
    return [s.strip().upper() for s in (raw or "").split(",") if s.strip()]


def _parse_regions(raw: str | None) -> list[str]:
    """Kommagetrennte Markt-Bereich-Liste aus der DB → Liste (mind. ein Eintrag)."""
    keys = [s.strip() for s in (raw or "").split(",") if s.strip()]
    return keys or ["sp500"]


def get_or_create_user(user_id: int, username: str | None = None) -> dict:
    """Holt das Nutzerprofil, legt bei Bedarf einen neuen 'in_progress'-Datensatz an."""
    now = db._utc_timestamp()
    with db._database().transaction() as transaction:
        transaction.execute(
            """INSERT INTO users
               (user_id, username, onboarding_state, created_at, updated_at)
               VALUES (:user_id, :username, 'in_progress', :created_at, :updated_at)
               ON CONFLICT (user_id) DO NOTHING""",
            {"user_id": user_id, "username": username,
             "created_at": now, "updated_at": now},
        )
        row = transaction.one("SELECT * FROM users WHERE user_id = :user_id", {"user_id": user_id})
    return _user_to_dict(row)


def get_user(user_id: int) -> dict | None:
    """Gibt das Nutzerprofil zurück oder None, falls nicht registriert."""
    database = db_backend.get_database(config.DB_BACKEND, db._connect)
    with database.transaction() as transaction:
        row = transaction.one("SELECT * FROM users WHERE user_id = :user_id", {"user_id": user_id})
    return _user_to_dict(row) if row else None


def save_profile(user_id: int, *, trade_size_eur: float,
                 broker_platform: str | None = None,
                 broker_api_key: str | None = None,
                 broker_api_secret: str | None = None):
    """Speichert das fertige Profil (verschlüsselt Broker-Zugangsdaten) und markiert es als 'complete'."""
    key_enc    = db.encrypt(broker_api_key) if broker_api_key else None
    secret_enc = db.encrypt(broker_api_secret) if broker_api_secret else None

    with db._database().transaction() as transaction:
        transaction.execute(
            """UPDATE users
               SET trade_size_eur = :trade_size_eur, broker_platform = :broker_platform,
                   broker_api_key = :broker_api_key, broker_api_secret = :broker_api_secret,
                   onboarding_state = 'complete', updated_at = :updated_at
               WHERE user_id = :user_id""",
            {"trade_size_eur": trade_size_eur, "broker_platform": broker_platform,
             "broker_api_key": key_enc, "broker_api_secret": secret_enc,
             "updated_at": db._utc_timestamp(), "user_id": user_id},
        )
    db.log.info(f"Profil gespeichert: user_id={user_id} (Broker: {broker_platform or '—'})")


def get_decrypted_credentials(user_id: int) -> tuple[str, str] | None:
    """Gibt (api_key, api_secret) entschlüsselt zurück, oder None falls kein Broker hinterlegt ist."""
    database = db_backend.get_database(config.DB_BACKEND, db._connect)
    with database.transaction() as transaction:
        row = transaction.one(
            "SELECT broker_api_key, broker_api_secret FROM users WHERE user_id = :user_id",
            {"user_id": user_id},
        )
    if not row or row["broker_api_key"] is None or row["broker_api_secret"] is None:
        return None
    return db.decrypt(row["broker_api_key"]), db.decrypt(row["broker_api_secret"])


# ── Broker-OAuth-Verbindungen (W4.4, PLAT-007) ───────────────────────────────
# Tokens werden verschlüsselt (Fernet, ENCRYPTION_KEY) gespeichert, nie im Klartext/in Logs.
# Paper/Live sind über die Spalte `mode` getrennt (unique je (user_id, mode)).

def store_broker_oauth_connection(*, user_id: int, mode: str, access_token: str,
                                  refresh_token: str | None, scopes: str,
                                  expires_at: str | None = None) -> None:
    """Speichert (verschlüsselt) eine OAuth-Verbindung je (user_id, mode); ersetzt eine bestehende."""
    now = db._utc_timestamp()
    with db._database().transaction() as transaction:
        transaction.execute(
            "DELETE FROM broker_oauth_connections WHERE user_id = :user_id AND mode = :mode",
            {"user_id": user_id, "mode": mode},
        )
        transaction.execute(
            """INSERT INTO broker_oauth_connections
               (user_id, mode, access_token, refresh_token, scopes, expires_at,
                created_at, updated_at, revoked_at)
               VALUES (:user_id, :mode, :access_token, :refresh_token, :scopes,
                       :expires_at, :created_at, :updated_at, NULL)""",
            {"user_id": user_id, "mode": mode,
             "access_token": db.encrypt(access_token),
             "refresh_token": db.encrypt(refresh_token) if refresh_token else None,
             "scopes": scopes, "expires_at": expires_at,
             "created_at": now, "updated_at": now},
        )


def get_broker_oauth_connection(user_id: int, mode: str) -> dict | None:
    """Gibt die aktive (nicht widerrufene) OAuth-Verbindung entschlüsselt zurück, sonst None."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            """SELECT access_token, refresh_token, scopes, expires_at, created_at, updated_at
               FROM broker_oauth_connections
               WHERE user_id = :user_id AND mode = :mode AND revoked_at IS NULL""",
            {"user_id": user_id, "mode": mode},
        )
    if not row:
        return None
    return {
        "user_id": user_id, "mode": mode,
        "access_token": db.decrypt(row["access_token"]),
        "refresh_token": db.decrypt(row["refresh_token"]) if row["refresh_token"] else None,
        "scopes": row["scopes"], "expires_at": row["expires_at"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def disconnect_broker_oauth_connection(user_id: int, mode: str) -> None:
    """Löscht die lokale OAuth-Verbindung (kein Broker-Revoke)."""
    with db._database().transaction() as transaction:
        transaction.execute(
            "DELETE FROM broker_oauth_connections WHERE user_id = :user_id AND mode = :mode",
            {"user_id": user_id, "mode": mode},
        )


def revoke_broker_oauth_connection(user_id: int, mode: str) -> None:
    """Markiert die Verbindung als widerrufen und überschreibt die Tokens (kein nutzbares Token bleibt)."""
    now = db._utc_timestamp()
    with db._database().transaction() as transaction:
        transaction.execute(
            """UPDATE broker_oauth_connections
               SET revoked_at = :now, access_token = :empty, refresh_token = NULL,
                   updated_at = :now
               WHERE user_id = :user_id AND mode = :mode AND revoked_at IS NULL""",
            {"now": now, "empty": db.encrypt(""), "user_id": user_id, "mode": mode},
        )


def list_active_users() -> list[dict]:
    """Gibt alle aktiven, vollständig eingerichteten Nutzer zurück (Empfänger der täglichen Jobs)."""
    database = db_backend.get_database(config.DB_BACKEND, db._connect)
    with database.transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM users WHERE is_active = 1 AND onboarding_state = 'complete'"
        )
    return [_user_to_dict(r) for r in rows]


def set_user_active(user_id: int, active: bool):
    """Aktiviert/deaktiviert einen Nutzer (z. B. für ein künftiges /stop)."""
    _update_user("UPDATE users SET is_active = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=1 if active else 0, user_id=user_id)


def set_market_region(user_id: int, region: str):
    """Setzt den Markt-Bereich des Nutzers auf genau einen Korb (ersetzt die Auswahl)."""
    _update_user("UPDATE users SET market_region = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=region, user_id=user_id)


def toggle_region(user_id: int, key: str) -> list[str]:
    """Schaltet einen Markt-Korb in der Auswahl des Nutzers an/aus (mind. einer bleibt aktiv).
    Gibt die neue Liste zurück."""
    def mutate(raw):
        keys = _parse_regions(raw)
        if key in keys:
            if len(keys) > 1:          # der letzte Korb bleibt erhalten
                keys.remove(key)
        else:
            keys.append(key)
        return ",".join(keys)
    value = _mutate_user_text(user_id, "market_region", mutate)
    return _parse_regions(value)


def set_trade_size(user_id: int, eur: float) -> float:
    """Setzt die Demo-Trade-Größe in € (auf 1..1.000.000 begrenzt). Gibt den gespeicherten Wert zurück."""
    eur = max(1.0, min(1_000_000.0, float(eur)))
    _update_user("UPDATE users SET trade_size_eur = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=eur, user_id=user_id)
    return eur


def set_top_n(user_id: int, n: int):
    """Setzt die gewünschte Anzahl täglicher Signale (auf 1..20 begrenzt)."""
    n = max(1, min(20, int(n)))
    _update_user("UPDATE users SET top_n_signals = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=n, user_id=user_id)


def set_sl_tp_mode(user_id: int, mode: str):
    """Setzt den SL/TP-Modus des Nutzers ('aus' | 'passiv' | 'normal' | 'aggressiv')."""
    _update_user("UPDATE users SET sl_tp_mode = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=mode, user_id=user_id)


def set_leverage(user_id: int, leverage: float):
    """Setzt den Standard-Hebel des Nutzers — serverseitig hart auf `MAX_LEVERAGE` begrenzt
    (TSAFE-002: kein UI-/Telegram-Wert kann das umgehen, Default 1×)."""
    leverage = max(1.0, min(db.MAX_LEVERAGE, float(leverage)))
    _update_user("UPDATE users SET leverage = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=leverage, user_id=user_id)


def set_auto_accept(user_id: int, on: bool):
    """Aktiviert/deaktiviert das automatische Annehmen neuer Signale."""
    _update_user("UPDATE users SET auto_accept = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=1 if on else 0, user_id=user_id)


def set_auto_universe(user_id: int, on: bool):
    """Schaltet das Voll-Universum (automatisch geladene Vollliste) an/aus.
    Aus → es wird der kuratierte Korb aus config.py genutzt (schnellere Analyse)."""
    _update_user("UPDATE users SET auto_universe = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=1 if on else 0, user_id=user_id)


def set_strategy(user_id: int, strategy: str):
    """Setzt die aktiven Signal-Strategien des Nutzers (ein Schlüssel oder kommagetrennte Liste)."""
    _update_user("UPDATE users SET strategy = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=strategy, user_id=user_id)


def set_llm_rank(user_id: int, on: bool):
    """Aktiviert/deaktiviert das LLM-Ranking (Claude Haiku) für den Nutzer."""
    _update_user("UPDATE users SET llm_rank = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=1 if on else 0, user_id=user_id)


def set_eod_close(user_id: int, on: bool):
    """Tagesende-Schließung an/aus. Aus → Trades über Nacht halten (nur SL/TP schließt)."""
    _update_user("UPDATE users SET eod_close = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=1 if on else 0, user_id=user_id)


def set_signal_window(user_id: int, on: bool):
    """15-Minuten-Annahmefenster an/aus. Aus (Default) → Signale bleiben den ganzen Handelstag annehmbar."""
    _update_user("UPDATE users SET signal_window = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=1 if on else 0, user_id=user_id)


def set_broker_exec(user_id: int, on: bool):
    """Echte (Paper-)Order-Ausführung über Alpaca an/aus (Default aus = nur Demo)."""
    _update_user("UPDATE users SET broker_exec = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=1 if on else 0, user_id=user_id)


def set_alpaca_credentials(user_id: int, api_key: str, api_secret: str):
    """Speichert die Alpaca-API-Zugangsdaten des Nutzers verschlüsselt (broker_platform='alpaca')."""
    _update_user(
        """UPDATE users SET broker_platform = 'alpaca', broker_api_key = :api_key,
           broker_api_secret = :api_secret, updated_at = :updated_at
           WHERE user_id = :user_id""",
        api_key=db.encrypt(api_key), api_secret=db.encrypt(api_secret), user_id=user_id,
    )
    db.log.info(f"Alpaca-Zugangsdaten gesetzt: user_id={user_id}")


def clear_alpaca_credentials(user_id: int):
    """Entfernt die Alpaca-Zugangsdaten und schaltet die Broker-Ausführung ab."""
    _update_user(
        """UPDATE users SET broker_platform = NULL, broker_api_key = NULL,
           broker_api_secret = NULL, broker_exec = 0, updated_at = :updated_at
           WHERE user_id = :user_id""", user_id=user_id,
    )
    db.log.info(f"Alpaca-Zugangsdaten entfernt: user_id={user_id}")


def has_alpaca_credentials(user_id: int) -> bool:
    """True, wenn der Nutzer eigene Alpaca-Zugangsdaten hinterlegt hat."""
    database = db_backend.get_database(config.DB_BACKEND, db._connect)
    with database.transaction() as transaction:
        row = transaction.one(
            "SELECT broker_platform, broker_api_key FROM users WHERE user_id = :user_id",
            {"user_id": user_id},
        )
    return bool(row and row["broker_platform"] == "alpaca" and row["broker_api_key"] is not None)


def toggle_strategy(user_id: int, key: str) -> list[str]:
    """Schaltet eine Strategie in der Auswahl des Nutzers an/aus (mind. eine bleibt aktiv).
    Gibt die neue Liste zurück."""
    def mutate(raw):
        keys = _parse_strategies(raw)
        if key in keys:
            if len(keys) > 1:          # die letzte Strategie nicht abschaltbar
                keys.remove(key)
        else:
            keys.append(key)
        return ",".join(keys)
    return _parse_strategies(_mutate_user_text(user_id, "strategy", mutate))


def add_watchlist_tickers(user_id: int, tickers: list[str]) -> list[str]:
    """Fügt Symbole zur persönlichen Watchlist hinzu (dedupliziert, großgeschrieben, sortiert).
    Gibt die neue Liste zurück."""
    def mutate(raw):
        current = set(_parse_watchlist(raw))
        current.update(t.strip().upper() for t in tickers if t.strip())
        return ",".join(sorted(current))
    return _parse_watchlist(_mutate_user_text(user_id, "watchlist", mutate))


def remove_watchlist_ticker(user_id: int, ticker: str) -> list[str]:
    """Entfernt ein Symbol aus der persönlichen Watchlist. Gibt die neue Liste zurück."""
    target = ticker.strip().upper()
    def mutate(raw):
        return ",".join(t for t in _parse_watchlist(raw) if t != target)
    return _parse_watchlist(_mutate_user_text(user_id, "watchlist", mutate))


# ── Benachrichtigungs-Kanal & In-App-Benachrichtigungen ──────────────────────

def set_notify_channel(user_id: int, channel: str) -> str:
    """Setzt den Benachrichtigungs-Kanal ('telegram' | 'web' | 'both'). Gibt den gültigen Wert zurück."""
    channel = channel if channel in ("telegram", "web", "both") else "both"
    _update_user("UPDATE users SET notify_channel = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=channel, user_id=user_id)
    return channel


def set_asset_pref(user_id: int, asset_pref: str) -> str:
    """Setzt die zuletzt gewählte Asset-Klasse (Dropdown auf der Website)."""
    asset_pref = (asset_pref or "stocks").strip() or "stocks"
    _update_user("UPDATE users SET asset_pref = :value, updated_at = :updated_at "
                 "WHERE user_id = :user_id", value=asset_pref, user_id=user_id)
    return asset_pref


def add_notification(user_id: int, title: str, body: str = "", type: str = "info") -> int:
    """Schreibt eine In-App-Benachrichtigung. Gibt die neue id zurück."""
    with db._database().transaction() as transaction:
        return transaction.insert_id(
            "INSERT INTO notifications (user_id, ts, type, title, body) "
            "VALUES (:user_id, :ts, :type, :title, :body)",
            {"user_id": user_id, "ts": db._utc_timestamp(), "type": type,
             "title": title, "body": body},
        )


def get_notifications(user_id: int, limit: int = 50) -> list[dict]:
    """Letzte Benachrichtigungen eines Nutzers (neueste zuerst)."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT id, ts, type, title, body, read FROM notifications "
            "WHERE user_id = :user_id ORDER BY id DESC LIMIT :limit",
            {"user_id": user_id, "limit": int(limit)},
        )
    return [{"id": r["id"], "ts": r["ts"], "type": r["type"], "title": r["title"],
             "body": r["body"], "read": bool(r["read"])} for r in rows]


def unread_count(user_id: int) -> int:
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT COUNT(*) AS n FROM notifications WHERE user_id = :user_id AND read = 0",
            {"user_id": user_id},
        )
    return row["n"] if row else 0


def mark_notifications_read(user_id: int):
    with db._database().transaction() as transaction:
        transaction.execute(
            "UPDATE notifications SET read = 1 WHERE user_id = :user_id", {"user_id": user_id}
        )
