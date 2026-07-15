"""Persistenter Kill-Switch-Verlauf (W1.3).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kill_switches",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("activated_by", sa.Text(), nullable=False),
        sa.Column("activated_at", sa.Text(), nullable=False),
        sa.Column("deactivated_by", sa.Text(), nullable=True),
        sa.Column("deactivated_at", sa.Text(), nullable=True),
        sa.CheckConstraint("scope IN ('global', 'user')", name="ck_kill_switches_scope"),
        sa.CheckConstraint(
            "(scope = 'global' AND user_id IS NULL) OR (scope = 'user' AND user_id IS NOT NULL)",
            name="ck_kill_switches_scope_user",
        ),
    )
    op.create_index(
        "idx_kill_switches_active", "kill_switches", ["active", "scope", "user_id", "id"]
    )
    op.create_index(
        "uq_kill_switches_active_global", "kill_switches", ["scope"], unique=True,
        postgresql_where=sa.text("active = true AND scope = 'global'"),
        sqlite_where=sa.text("active = 1 AND scope = 'global'"),
    )
    op.create_index(
        "uq_kill_switches_active_user", "kill_switches", ["user_id"], unique=True,
        postgresql_where=sa.text("active = true AND scope = 'user'"),
        sqlite_where=sa.text("active = 1 AND scope = 'user'"),
    )


def downgrade() -> None:
    op.drop_index("uq_kill_switches_active_user", table_name="kill_switches")
    op.drop_index("uq_kill_switches_active_global", table_name="kill_switches")
    op.drop_index("idx_kill_switches_active", table_name="kill_switches")
    op.drop_table("kill_switches")
