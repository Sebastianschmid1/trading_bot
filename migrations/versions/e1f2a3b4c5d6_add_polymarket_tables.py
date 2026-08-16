"""add polymarket_markets + polymarket_snapshots (PM-0, read-only Ereignissensor)

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5

Reine Datenschicht (docs/POLYMARKET_INTEGRATION.md §3): Marktstammdaten
(``polymarket_markets``) und bewertete Zeitreihen-Snapshots (``polymarket_snapshots``).
Spaltentypen/Defaults spiegeln ``SCHEMA_SQL`` in ``core/db.py``; Zeitspalten folgen dem
naiven UTC-Vertrag (von ``core/db.py::_polymarket_timestamp`` gebunden, kein
``server_default=CURRENT_TIMESTAMP``). Berührt keinen TSAFE-Pfad, keinen Signal-/Order-/
Anzeigepfad — PM-0 ist vollständig wirkungslos.
"""

from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "polymarket_markets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("condition_id", sa.Text(), nullable=False),
        sa.Column("token_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("slug", sa.Text(), nullable=False, server_default=""),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution_source", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution_rules", sa.Text(), nullable=False, server_default=""),
        sa.Column("end_date", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("condition_id", name="uq_polymarket_markets_condition_id"),
    )
    op.create_table(
        "polymarket_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("condition_id", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("bid", sa.Float(), nullable=True),
        sa.Column("ask", sa.Float(), nullable=True),
        sa.Column("mid", sa.Float(), nullable=True),
        sa.Column("spread_bps", sa.Float(), nullable=True),
        sa.Column("depth_bid_usd", sa.Float(), nullable=True),
        sa.Column("depth_ask_usd", sa.Float(), nullable=True),
        sa.Column("volume_24h_usd", sa.Float(), nullable=True),
        sa.Column("liquidity_usd", sa.Float(), nullable=True),
        sa.Column("trade_count_24h", sa.BigInteger(), nullable=True),
        sa.Column("last_trade_at", sa.Text(), nullable=True),
        sa.Column("usable", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reject_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_file_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_polymarket_snapshots_condition", "polymarket_snapshots",
        ["condition_id", "fetched_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_polymarket_snapshots_condition", table_name="polymarket_snapshots")
    op.drop_table("polymarket_snapshots")
    op.drop_table("polymarket_markets")
