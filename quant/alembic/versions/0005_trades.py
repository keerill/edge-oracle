"""add trades hypertable

Revision ID: 0005_trades
Revises: 0004_calibration
Create Date: 2026-06-17

Trade prints (Data API /trades) — a TimescaleDB hypertable on ``time`` like ``quotes``.
Mirrors db/tables.py. All money is unbounded NUMERIC (Decimal end-to-end).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_trades"
down_revision: str | None = "0004_calibration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trades",
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("token_id", sa.Text, nullable=False),
        sa.Column("market_id", sa.Text, nullable=False),
        sa.Column("price", sa.Numeric, nullable=False),
        sa.Column("size", sa.Numeric, nullable=False),
        sa.Column("taker_side", sa.Text, nullable=True),
        sa.Column("trade_id", sa.Text, nullable=False),
    )
    # Convert to a hypertable AFTER the table exists; build the index AFTER conversion.
    op.execute("SELECT create_hypertable('trades', 'time', if_not_exists => TRUE);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trades_token_time ON trades (token_id, time DESC);"
    )


def downgrade() -> None:
    op.drop_table("trades")  # drops the hypertable
