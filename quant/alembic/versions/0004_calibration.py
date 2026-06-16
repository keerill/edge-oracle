"""add calibration journal table

Revision ID: 0004_calibration
Revises: 0003_price_signals
Create Date: 2026-06-16

A plain (non-hypertable) table for resolved predictions — one row per market
resolution, scored offline for Brier / log-loss / reliability. Mirrors ``db/tables.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_calibration"
down_revision: str | None = "0003_price_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calibration",
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("market_id", sa.Text, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("strategy", sa.Text, nullable=False),
        sa.Column("estimate", sa.Numeric, nullable=False),
        sa.Column("price", sa.Numeric, nullable=False),
        sa.Column("outcome", sa.SmallInteger, nullable=False),
        sa.CheckConstraint("outcome IN (0, 1)", name="ck_calibration_outcome"),
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_calibration_time ON calibration (time DESC);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_calibration_strategy_time "
        "ON calibration (strategy, time DESC);"
    )


def downgrade() -> None:
    op.drop_table("calibration")  # drops its indexes too
