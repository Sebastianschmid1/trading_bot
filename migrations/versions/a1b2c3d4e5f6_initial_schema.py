"""Initiales Zielschema für PostgreSQL (PLAT-001)

Spiegelt 1:1 das eingefrorene SQLite-Schema aus docs/DB_SCHEMA_SQLITE.md (Stand
2026-07-14). Reine Schemaerstellung — die eigentliche Datenübernahme (Export →
Transformation → Testmigration → Zeilen/Summen-Vergleich) ist ein eigener,
späterer Checklisten-Punkt (docs/PLAN_CHECKLIST.md Phase 1).

Die BigInteger-Korrektur für Telegram-IDs und wachsende interne IDs wird direkt
hier gepflegt: Diese initiale Migration wurde noch auf keiner produktiven
PostgreSQL-Instanz angewendet, daher ist keine nachgelagerte Korrekturrevision nötig.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("trade_size_eur", sa.Float(), nullable=False, server_default="25.0"),
        sa.Column("broker_platform", sa.Text(), nullable=True),
        sa.Column("broker_api_key", sa.LargeBinary(), nullable=True),
        sa.Column("broker_api_secret", sa.LargeBinary(), nullable=True),
        sa.Column("onboarding_state", sa.Text(), nullable=False, server_default="in_progress"),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("dashboard_token", sa.Text(), nullable=True),
        sa.Column("market_region", sa.Text(), nullable=False, server_default="sp500"),
        sa.Column("top_n_signals", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("sl_tp_mode", sa.Text(), nullable=False, server_default="normal"),
        sa.Column("leverage", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("auto_accept", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_universe", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("strategy", sa.Text(), nullable=False, server_default="standard"),
        sa.Column("llm_rank", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("eod_close", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("broker_exec", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signal_window", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("watchlist", sa.Text(), nullable=False, server_default=""),
        sa.Column("notify_channel", sa.Text(), nullable=False, server_default="both"),
        sa.Column("asset_pref", sa.Text(), nullable=False, server_default="stocks"),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=True),
    )

    op.create_table(
        "trades",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("signal_json", sa.Text(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("entry", sa.Float(), nullable=True),
        sa.Column("exit", sa.Float(), nullable=True),
        sa.Column("pnl_eur", sa.Float(), nullable=True),
        sa.Column("pnl_pct", sa.Float(), nullable=True),
        sa.Column("broker_order_id", sa.Text(), nullable=True),
        sa.Column("broker_status", sa.Text(), nullable=True),
        sa.Column("broker_filled_qty", sa.Float(), nullable=True),
        sa.Column("broker_filled_avg_price", sa.Float(), nullable=True),
        sa.Column("broker_updated_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.UniqueConstraint("user_id", "trade_date", "ticker", name="uq_trades_user_date_ticker"),
    )
    op.create_index(
        "idx_trades_user_date_status", "trades", ["user_id", "trade_date", "status"]
    )

    op.create_table(
        "trade_ticks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("ts", sa.Text(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("strength", sa.Float(), nullable=True),
    )
    op.create_index(
        "idx_ticks_user_date_ticker", "trade_ticks", ["user_id", "trade_date", "ticker", "ts"]
    )

    op.create_table(
        "trades_archive",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("signal_json", sa.Text(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("entry", sa.Float(), nullable=True),
        sa.Column("exit", sa.Float(), nullable=True),
        sa.Column("pnl_eur", sa.Float(), nullable=True),
        sa.Column("pnl_pct", sa.Float(), nullable=True),
        sa.Column("broker_order_id", sa.Text(), nullable=True),
        sa.Column("broker_status", sa.Text(), nullable=True),
        sa.Column("broker_filled_qty", sa.Float(), nullable=True),
        sa.Column("broker_filled_avg_price", sa.Float(), nullable=True),
        sa.Column("broker_updated_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.Text(), nullable=False),
        sa.Column("archive_reason", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_trades_archive_user_date_status",
        "trades_archive", ["user_id", "trade_date", "status"],
    )

    op.create_table(
        "trade_ticks_archive",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("ts", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("strength", sa.Float(), nullable=True),
        sa.Column("archived_at", sa.Text(), nullable=False),
        sa.Column("archive_reason", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_ticks_archive_user_date_ticker",
        "trade_ticks_archive", ["user_id", "trade_date", "ticker", "ts"],
    )

    op.create_table(
        "sessions",
        sa.Column("token", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.Text(), nullable=False),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False, server_default="info"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("read", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("idx_notifications_user", "notifications", ["user_id", "read", "id"])

    op.create_table(
        "strategy_configs",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("params_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.Text(), nullable=True),
    )

    op.create_table(
        "trade_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("trade_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("broker_status", sa.Text(), nullable=True),
        sa.Column("ts", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("idx_trade_events_trade", "trade_events", ["trade_id", "ts"])
    op.create_index("idx_trade_events_user", "trade_events", ["user_id", "ts"])


def downgrade() -> None:
    op.drop_table("trade_events")
    op.drop_table("strategy_configs")
    op.drop_index("idx_notifications_user", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("sessions")
    op.drop_index("idx_ticks_archive_user_date_ticker", table_name="trade_ticks_archive")
    op.drop_table("trade_ticks_archive")
    op.drop_index("idx_trades_archive_user_date_status", table_name="trades_archive")
    op.drop_table("trades_archive")
    op.drop_index("idx_ticks_user_date_ticker", table_name="trade_ticks")
    op.drop_table("trade_ticks")
    op.drop_index("idx_trades_user_date_status", table_name="trades")
    op.drop_table("trades")
    op.drop_table("users")
