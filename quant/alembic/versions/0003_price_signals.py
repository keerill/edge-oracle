"""generalize signals with a strategy tag

Revision ID: 0003_price_signals
Revises: 0002_signals
Create Date: 2026-06-16

Adds a ``strategy`` discriminator to ``signals`` so several strategies share one table,
makes the set-arb money columns nullable (price-only strategies don't populate them), and
adds the price-signal columns (``price``/``edge_score``/``fair_value``). Mirrors
``db/tables.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_price_signals"
down_revision: str | None = "0002_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Set-arb money columns: NOT NULL originally, relaxed to nullable here so price-only
# strategies can leave them empty. Restored to NOT NULL on downgrade.
_ARB_COLS = (
    "yes_price",
    "no_price",
    "set_size",
    "gross_edge",
    "estimated_costs",
    "net_edge",
    "hypothetical_pnl",
)


def upgrade() -> None:
    # server_default backfills any existing rows to 'set_arb' and lets the untouched
    # arb insert (which omits ``strategy``) keep working.
    op.add_column(
        "signals",
        sa.Column("strategy", sa.Text, nullable=False, server_default=sa.text("'set_arb'")),
    )
    for col in _ARB_COLS:
        op.alter_column("signals", col, existing_type=sa.Numeric, nullable=True)
    op.add_column("signals", sa.Column("price", sa.Numeric, nullable=True))
    op.add_column("signals", sa.Column("edge_score", sa.Numeric, nullable=True))
    op.add_column("signals", sa.Column("fair_value", sa.Numeric, nullable=True))
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_signals_strategy_time ON signals (strategy, time DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_signals_strategy_time;")
    # Non-arb rows have NULL arb columns; drop them so restoring NOT NULL is valid.
    op.execute("DELETE FROM signals WHERE strategy <> 'set_arb';")
    op.drop_column("signals", "fair_value")
    op.drop_column("signals", "edge_score")
    op.drop_column("signals", "price")
    for col in _ARB_COLS:
        op.alter_column("signals", col, existing_type=sa.Numeric, nullable=False)
    op.drop_column("signals", "strategy")
