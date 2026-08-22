"""add trades.high_water (Hoechstkurs seit Einstieg fuer den ATR-Trailing-Stop)

Revision ID: a2b3c4d5e6f7
Revises: e1f2a3b4c5d6

Der ATR-Trailing-Stop in ``market/exit_policies.py::_trailing_stop`` braucht den
Hoechstkurs seit Einstieg. Der wurde bisher nirgends mitgefuehrt, weshalb der Aufruf in
``tgbot/bot.py`` ``highest_price_since_entry=None`` uebergab und der Trailing-Stop
strukturell nie ausloesen konnte — auch nicht mit ``STRATEGY_EXITS_ENABLED=true``.

Bewusst eine eigene Spalte statt einer Auswertung von ``trade_ticks``: die Tick-Tabelle ist
nach ``trade_date`` partitioniert und wird beim Archivieren geleert, taugt also nicht als
Grundlage fuer einen Exit, der echtes Geld bewegt.

Nullable ohne Default: bestehende offene Positionen starten leer und werden beim naechsten
Monitor-Tick mit dem dann aktuellen Kurs initialisiert. Der Trailing-Stop liegt danach eine
ATR-Spanne darunter, kann also nicht verfrueht ausloesen. Spiegelt ``SCHEMA_SQL`` in
``core/db.py``. Aendert fuer sich genommen kein Handelsverhalten — der Exit haengt
unveraendert am Schalter ``STRATEGY_EXITS_ENABLED`` (Default aus).
"""

from alembic import op
import sqlalchemy as sa

revision = "a2b3c4d5e6f7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("high_water", sa.Float(), nullable=True))
    # `trades_archive` spiegelt `trades` spaltengleich; die Archivierungs-Query in
    # `core/db.py::reset_user_trades` listet die Spalten einzeln auf und bricht sonst.
    op.add_column("trades_archive", sa.Column("high_water", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("trades_archive", "high_water")
    op.drop_column("trades", "high_water")
