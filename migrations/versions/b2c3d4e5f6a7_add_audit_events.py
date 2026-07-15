"""Persistentes Audit-Log (append-only) — audit_events (W1.4 / Gate P1.1)

Eigene Revision NACH der Initial-Migration: die Prod-PostgreSQL-Instanz steht seit
dem Cutover (2026-07-15) bereits auf a1b2c3d4e5f6, daher darf audit_events NICHT in
die schon angewendete Initial-Migration, sondern muss als nachgelagerte Revision
kommen (sonst wird die Tabelle auf der bestehenden Prod-DB nie angelegt).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Text(), nullable=False, unique=True),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("old_state", sa.Text(), nullable=True),
        sa.Column("new_state", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("source_channel", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_audit_events_entity", "audit_events", ["entity_type", "entity_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("idx_audit_events_entity", table_name="audit_events")
    op.drop_table("audit_events")
