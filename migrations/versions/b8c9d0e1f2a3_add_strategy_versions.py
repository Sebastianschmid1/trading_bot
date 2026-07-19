"""add strategy_versions (W3.3, persistente Strategieversionierung → Gate P5)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_key", sa.Text(), nullable=False),
        sa.Column("strategy_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("feature_version", sa.Text(), nullable=False, server_default="unversioned"),
        sa.Column("universe_version", sa.Text(), nullable=False, server_default="unversioned"),
        sa.Column("entry_rules", sa.Text(), nullable=False, server_default=""),
        sa.Column("exit_rules", sa.Text(), nullable=False, server_default=""),
        sa.Column("cost_model_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("release_status", sa.Text(), nullable=False, server_default="live"),
        sa.Column("code_commit", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("strategy_key", "content_hash",
                            name="uq_strategy_versions_key_hash"),
    )
    op.create_index("idx_strategy_versions_key", "strategy_versions", ["strategy_key"])


def downgrade() -> None:
    op.drop_index("idx_strategy_versions_key", table_name="strategy_versions")
    op.drop_table("strategy_versions")
