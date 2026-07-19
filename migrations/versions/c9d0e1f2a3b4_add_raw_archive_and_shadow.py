"""add raw_data_archive + shadow_snapshots (W3.5, Shadow-Scheduler + Rohdatenarchiv-Metadaten)

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_data_archive",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("trading_date", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("symbol", "trading_date", "timeframe",
                            name="uq_raw_data_archive_partition"),
    )
    op.create_table(
        "shadow_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_version_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.Text(), nullable=False),
        sa.Column("net_pnl", sa.Float(), nullable=True),
        sa.Column("open_risk", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_shadow_snapshots_captured", "shadow_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("idx_shadow_snapshots_captured", table_name="shadow_snapshots")
    op.drop_table("shadow_snapshots")
    op.drop_table("raw_data_archive")
