"""add portfolio positions table

Revision ID: 0007_positions
Revises: 0006_user_config
Create Date: 2026-06-17

One row per bet the operator placed (manually on Polymarket), tracked for live P&L and
exposure. ``outcome``/``pnl``/``resolved_at`` stay NULL until the market resolves and the
position is settled. Mirrors ``db/tables.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_positions"
down_revision: str | None = "0006_user_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("market_id", sa.Text, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("strategy", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("entry_price", sa.Numeric, nullable=False),
        sa.Column("stake_usd", sa.Numeric, nullable=False),
        sa.Column("shares", sa.Numeric, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'open'")),
        sa.Column("outcome", sa.SmallInteger, nullable=True),
        sa.Column("pnl", sa.Numeric, nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("signal_id", sa.Text, nullable=True),
        sa.CheckConstraint("outcome IS NULL OR outcome IN (0, 1)", name="ck_positions_outcome"),
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_positions_status ON positions (status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_positions_condition ON positions (condition_id);")


def downgrade() -> None:
    op.drop_table("positions")
