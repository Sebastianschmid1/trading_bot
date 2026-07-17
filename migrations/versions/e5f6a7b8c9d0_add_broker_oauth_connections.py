"""add broker_oauth_connections (W4.4, PLAT-007)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_oauth_connections",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        # Tokens werden verschlüsselt abgelegt (Fernet) — daher Binärspalten.
        sa.Column("access_token", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token", sa.LargeBinary(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.CheckConstraint("mode IN ('paper', 'live')", name="ck_broker_oauth_mode"),
    )
    op.create_index(
        "uq_broker_oauth_user_mode", "broker_oauth_connections",
        ["user_id", "mode"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_broker_oauth_user_mode", table_name="broker_oauth_connections")
    op.drop_table("broker_oauth_connections")
