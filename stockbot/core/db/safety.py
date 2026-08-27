"""Sicherheitszustand: Audit-Log, Kill-Switch und Risikoprofile.

Das Audit-Log ist append-only und die Beweiskette für jede sicherheitsrelevante
Entscheidung. Der Kill-Switch liegt persistent in der Datenbank, damit ein Neustart ihn
nicht stillschweigend aufhebt. Die Risikoprofile halten die Grenzen, gegen die der Risk
Service jede Order prüft.
"""

import json
from datetime import datetime, timezone

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


# -- Persistentes Audit-Log ---------------------------------------------------

def _audit_timestamp(value: str) -> str:
    """Normalisiert ISO-Zeitstempel auf den backend-neutralen naiven UTC-Vertrag."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return db._utc_timestamp(parsed)


def append_audit_event(event):
    """Hängt ein AuditEvent unveränderlich an; es gibt bewusst keinen Mutationspfad."""
    with db._database().transaction() as transaction:
        transaction.execute(
            """INSERT INTO audit_events
               (event_id, timestamp, user_id, actor, entity_type, entity_id, action,
                old_state, new_state, trace_id, source_channel, metadata_json)
               VALUES (:event_id, :timestamp, :user_id, :actor, :entity_type, :entity_id,
                       :action, :old_state, :new_state, :trace_id, :source_channel,
                       :metadata_json)""",
            {"event_id": event.event_id, "timestamp": _audit_timestamp(event.timestamp),
             "user_id": event.user_id, "actor": event.actor,
             "entity_type": event.entity_type, "entity_id": event.entity_id,
             "action": event.action, "old_state": event.old_state,
             "new_state": event.new_state, "trace_id": event.trace_id,
             "source_channel": event.source_channel,
             "metadata_json": json.dumps(event.metadata, sort_keys=True, default=str)},
        )
    return event


def _as_audit_event(row: dict):
    from stockbot.core.domain import AuditEvent

    return AuditEvent(
        event_id=str(row["event_id"]), timestamp=_audit_timestamp(str(row["timestamp"])),
        user_id=row["user_id"], actor=str(row["actor"]),
        entity_type=str(row["entity_type"]), entity_id=str(row["entity_id"]),
        action=str(row["action"]), old_state=row["old_state"], new_state=row["new_state"],
        trace_id=str(row["trace_id"]), source_channel=str(row["source_channel"]),
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def audit_events_for_entity(entity_type: str, entity_id: str) -> list:
    with db._database().transaction() as transaction:
        rows = transaction.all(
            """SELECT * FROM audit_events
               WHERE entity_type = :entity_type AND entity_id = :entity_id ORDER BY id""",
            {"entity_type": entity_type, "entity_id": entity_id},
        )
    return [_as_audit_event(row) for row in rows]


def all_audit_events() -> list:
    with db._database().transaction() as transaction:
        rows = transaction.all("SELECT * FROM audit_events ORDER BY id")
    return [_as_audit_event(row) for row in rows]


# -- Persistenter Kill-Switch -------------------------------------------------

def _kill_switch_timestamp(value: str | None) -> str | None:
    """Normalisiert SQLite- und PostgreSQL-Zeitwerte auf naives UTC."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return db._utc_timestamp(parsed)


def activate_kill_switch(*, scope: str, user_id: int | None, reason: str,
                         activated_by: str, activated_at: str):
    """Schließt einen alten aktiven Eintrag und hängt die neue Aktivierung an."""
    timestamp = _kill_switch_timestamp(activated_at)
    with db._database().transaction() as transaction:
        transaction.execute(
            """UPDATE kill_switches
               SET active = :inactive, deactivated_by = :deactivated_by,
                   deactivated_at = :deactivated_at
               WHERE scope = :scope AND active = :active
                 AND ((:user_id IS NULL AND user_id IS NULL) OR user_id = :user_id)""",
            {"inactive": False, "deactivated_by": activated_by,
             "deactivated_at": timestamp, "scope": scope, "active": True,
             "user_id": user_id},
        )
        row_id = transaction.insert_id(
            """INSERT INTO kill_switches
               (scope, user_id, active, reason, activated_by, activated_at,
                deactivated_by, deactivated_at)
               VALUES (:scope, :user_id, :active, :reason, :activated_by,
                       :activated_at, :deactivated_by, :deactivated_at)""",
            {"scope": scope, "user_id": user_id, "active": True, "reason": reason,
             "activated_by": activated_by, "activated_at": timestamp,
             "deactivated_by": None, "deactivated_at": None},
        )
        return transaction.one("SELECT * FROM kill_switches WHERE id = :id", {"id": row_id})


def deactivate_kill_switch(*, scope: str, user_id: int | None,
                           deactivated_by: str, deactivated_at: str):
    timestamp = _kill_switch_timestamp(deactivated_at)
    with db._database().transaction() as transaction:
        row = transaction.one(
            """SELECT * FROM kill_switches
               WHERE scope = :scope AND active = :active
                 AND ((:user_id IS NULL AND user_id IS NULL) OR user_id = :user_id)
               ORDER BY id DESC LIMIT 1""",
            {"scope": scope, "active": True, "user_id": user_id},
        )
        if row is None:
            return None
        transaction.execute(
            """UPDATE kill_switches
               SET active = :active, deactivated_by = :deactivated_by,
                   deactivated_at = :deactivated_at WHERE id = :id""",
            {"active": False, "deactivated_by": deactivated_by,
             "deactivated_at": timestamp, "id": row["id"]},
        )
        return transaction.one("SELECT * FROM kill_switches WHERE id = :id", {"id": row["id"]})


def get_active_kill_switches() -> list[dict]:
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM kill_switches WHERE active = :active ORDER BY id",
            {"active": True},
        )
    for row in rows:
        row["activated_at"] = _kill_switch_timestamp(row["activated_at"])
        row["deactivated_at"] = _kill_switch_timestamp(row["deactivated_at"])
    return rows


def get_post_trade_risk_rows() -> tuple[list[dict], list[dict]]:
    """Lädt offene Trade-Positionen und aktive OMS-Orders in einer Seam-Transaktion."""
    with db._database().transaction() as transaction:
        positions = transaction.all(
            "SELECT * FROM trades WHERE status = 'active' ORDER BY user_id, ticker, id",
        )
        orders = transaction.all(
            """SELECT * FROM orders
               WHERE status IN ('submitted', 'accepted_by_broker', 'partially_filled',
                                'cancel_requested')
               ORDER BY user_id, ticker, id""",
        )
    return positions, orders


def get_risk_profile(user_id: int) -> "RiskProfile | None":
    """Lädt das persistierte Risikoprofil eines Nutzers oder ``None``, wenn keines existiert.

    ``None`` ist bewusst das Signal für „Nutzer hat kein eigenes Profil gespeichert": der
    Aufrufer fällt dann auf das permissive Default-`RiskProfile` zurück (unverändertes
    heutiges Verhalten). Es wird KEIN Default-Profil implizit angelegt.
    """
    from stockbot.core.domain import RiskProfile
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT * FROM risk_profiles WHERE user_id = :user_id", {"user_id": user_id})
    if not row:
        return None
    return RiskProfile(
        user_id=int(row["user_id"]),
        account_risk_per_trade_pct=float(row["account_risk_per_trade_pct"]),
        daily_loss_limit_pct=float(row["daily_loss_limit_pct"]),
        max_open_positions=int(row["max_open_positions"]),
        max_position_pct=float(row["max_position_pct"]),
        max_sector_exposure_pct=float(row["max_sector_exposure_pct"]),
        max_correlated_exposure_pct=float(row["max_correlated_exposure_pct"]),
        max_daily_new_exposure_pct=float(row["max_daily_new_exposure_pct"]),
        max_spread_bps=float(row["max_spread_bps"]),
        max_quote_age_seconds=int(row["max_quote_age_seconds"]),
        min_average_dollar_volume=float(row["min_average_dollar_volume"]),
        earnings_blackout_days=int(row["earnings_blackout_days"]),
        allow_overnight=bool(row["allow_overnight"]),
        allowed_strategies=tuple(json.loads(row["allowed_strategies_json"])),
    )


def save_risk_profile(profile: "RiskProfile") -> None:
    """Persistiert (upsert) das Risikoprofil eines Nutzers unter seiner ``user_id``.

    Zeitspalten folgen dem naiven UTC-Vertrag (`_utc_timestamp()`); ``created_at`` bleibt
    beim Update erhalten, nur ``updated_at`` wird fortgeschrieben.
    """
    now = db._utc_timestamp()
    with db._database().transaction() as transaction:
        transaction.execute(
            """INSERT INTO risk_profiles (
                   user_id, account_risk_per_trade_pct, daily_loss_limit_pct,
                   max_open_positions, max_position_pct, max_sector_exposure_pct,
                   max_correlated_exposure_pct, max_daily_new_exposure_pct, max_spread_bps,
                   max_quote_age_seconds, min_average_dollar_volume, earnings_blackout_days,
                   allow_overnight, allowed_strategies_json, created_at, updated_at)
               VALUES (
                   :user_id, :account_risk_per_trade_pct, :daily_loss_limit_pct,
                   :max_open_positions, :max_position_pct, :max_sector_exposure_pct,
                   :max_correlated_exposure_pct, :max_daily_new_exposure_pct, :max_spread_bps,
                   :max_quote_age_seconds, :min_average_dollar_volume, :earnings_blackout_days,
                   :allow_overnight, :allowed_strategies_json, :created_at, :updated_at)
               ON CONFLICT (user_id) DO UPDATE SET
                   account_risk_per_trade_pct = excluded.account_risk_per_trade_pct,
                   daily_loss_limit_pct = excluded.daily_loss_limit_pct,
                   max_open_positions = excluded.max_open_positions,
                   max_position_pct = excluded.max_position_pct,
                   max_sector_exposure_pct = excluded.max_sector_exposure_pct,
                   max_correlated_exposure_pct = excluded.max_correlated_exposure_pct,
                   max_daily_new_exposure_pct = excluded.max_daily_new_exposure_pct,
                   max_spread_bps = excluded.max_spread_bps,
                   max_quote_age_seconds = excluded.max_quote_age_seconds,
                   min_average_dollar_volume = excluded.min_average_dollar_volume,
                   earnings_blackout_days = excluded.earnings_blackout_days,
                   allow_overnight = excluded.allow_overnight,
                   allowed_strategies_json = excluded.allowed_strategies_json,
                   updated_at = excluded.updated_at""",
            {
                "user_id": int(profile.user_id),
                "account_risk_per_trade_pct": float(profile.account_risk_per_trade_pct),
                "daily_loss_limit_pct": float(profile.daily_loss_limit_pct),
                "max_open_positions": int(profile.max_open_positions),
                "max_position_pct": float(profile.max_position_pct),
                "max_sector_exposure_pct": float(profile.max_sector_exposure_pct),
                "max_correlated_exposure_pct": float(profile.max_correlated_exposure_pct),
                "max_daily_new_exposure_pct": float(profile.max_daily_new_exposure_pct),
                "max_spread_bps": float(profile.max_spread_bps),
                "max_quote_age_seconds": int(profile.max_quote_age_seconds),
                "min_average_dollar_volume": float(profile.min_average_dollar_volume),
                "earnings_blackout_days": int(profile.earnings_blackout_days),
                "allow_overnight": 1 if profile.allow_overnight else 0,
                "allowed_strategies_json": json.dumps(list(profile.allowed_strategies)),
                "created_at": now, "updated_at": now,
            },
        )
