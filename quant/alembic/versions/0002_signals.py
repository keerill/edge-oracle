"""add signals table

Revision ID: 0002_signals
Revises: 0001_init
Create Date: 2026-06-16

A plain (non-hypertable) table for detected set-arb opportunities — sparse and
append-only, so no TimescaleDB chunking is warranted. Mirrors ``db/tables.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_signals"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("market_id", sa.Text, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("yes_price", sa.Numeric, nullable=False),
        sa.Column("no_price", sa.Numeric, nullable=False),
        sa.Column("set_size", sa.Numeric, nullable=False),
        sa.Column("gross_edge", sa.Numeric, nullable=False),
        sa.Column("estimated_costs", sa.Numeric, nullable=False),
        sa.Column("net_edge", sa.Numeric, nullable=False),
        sa.Column("hypothetical_pnl", sa.Numeric, nullable=False),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_signals_market_time ON signals (market_id, time DESC);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_signals_time ON signals (time DESC);")


def downgrade() -> None:
    op.drop_table("signals")  # drops its indexes too
