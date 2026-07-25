"""add risk_profiles (persistiertes RiskProfile pro Nutzer, RISK-004-Wiring)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4

Legt die Persistenz fuer ein pro Nutzer gespeichertes Risikoprofil an. Ohne Eintrag faellt
der Loader auf das permissive Default-`RiskProfile` zurueck (inert-by-default). Spaltentypen/
Defaults spiegeln `SCHEMA_SQL` in `core/db.py`; Zeitspalten folgen dem naiven UTC-Vertrag
(vom Loader gebunden, kein server_default=CURRENT_TIMESTAMP).
"""

from alembic import op
import sqlalchemy as sa

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_profiles",
        sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("account_risk_per_trade_pct", sa.Float(), nullable=False, server_default="0.25"),
        sa.Column("daily_loss_limit_pct", sa.Float(), nullable=False, server_default="1.00"),
        sa.Column("max_open_positions", sa.BigInteger(), nullable=False, server_default="5"),
        sa.Column("max_position_pct", sa.Float(), nullable=False, server_default="100.0"),
        sa.Column("max_sector_exposure_pct", sa.Float(), nullable=False, server_default="100.0"),
        sa.Column("max_correlated_exposure_pct", sa.Float(), nullable=False, server_default="100.0"),
        sa.Column("max_daily_new_exposure_pct", sa.Float(), nullable=False, server_default="100.0"),
        sa.Column("max_spread_bps", sa.Float(), nullable=False, server_default="50.0"),
        sa.Column("max_quote_age_seconds", sa.BigInteger(), nullable=False, server_default="60"),
        sa.Column("min_average_dollar_volume", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("earnings_blackout_days", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("allow_overnight", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("allowed_strategies_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("risk_profiles")
