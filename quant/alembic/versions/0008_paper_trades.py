"""add paper-trading journal table

Revision ID: 0008_paper_trades
Revises: 0007_positions
Create Date: 2026-06-18

One row per advisory recommendation the system would have placed, auto-captured at advice
time (no money, no execution) so the strategy can be scored against real outcomes. The
no-money sibling of ``positions``. ``p``/``p_lo`` populate only for directional rows;
``outcome``/``realized_pnl``/``resolved_at`` stay NULL until settlement. Mirrors
``db/tables.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_paper_trades"
down_revision: str | None = "0007_positions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("advised_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("strategy", sa.Text, nullable=False),
        sa.Column("market_id", sa.Text, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("advised_price", sa.Numeric, nullable=False),
        sa.Column("stake_usd", sa.Numeric, nullable=False),
        sa.Column("shares", sa.Numeric, nullable=False),
        sa.Column("edge", sa.Numeric, nullable=False),
        sa.Column("p", sa.Numeric, nullable=True),
        sa.Column("p_lo", sa.Numeric, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'open'")),
        sa.Column("outcome", sa.SmallInteger, nullable=True),
        sa.Column("realized_pnl", sa.Numeric, nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("signal_id", sa.Text, nullable=True),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN (0, 1)", name="ck_paper_trades_outcome"
        ),
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_paper_trades_status ON paper_trades (status);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_paper_trades_condition ON paper_trades (condition_id);"
    )


def downgrade() -> None:
    op.drop_table("paper_trades")
