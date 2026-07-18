"""add callback_tokens (W7, Telegram-Callback-Sicherheit)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""

from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "callback_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.Text(), nullable=False, unique=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("used_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_callback_tokens_token", "callback_tokens", ["token"])


def downgrade() -> None:
    op.drop_index("idx_callback_tokens_token", table_name="callback_tokens")
    op.drop_table("callback_tokens")
