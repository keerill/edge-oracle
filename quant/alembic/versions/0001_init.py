"""init markets table + quotes hypertable

Revision ID: 0001_init
Revises:
Create Date: 2026-06-16

Ordering is load-bearing: enable the extension, create the regular tables, THEN
convert ``quotes`` to a hypertable, THEN build the time-series index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")

    op.create_table(
        "markets",
        sa.Column("market_id", sa.Text, primary_key=True),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=True),
        sa.Column("event_id", sa.Text, nullable=True),
        sa.Column("yes_token_id", sa.Text, nullable=False),
        sa.Column("no_token_id", sa.Text, nullable=False),
        sa.Column("enable_order_book", sa.Boolean, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("closed", sa.Boolean, nullable=False),
        sa.Column("tracked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("liquidity", sa.Numeric, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("condition_id", name="uq_markets_condition_id"),
    )
    op.create_index("ix_markets_tracked", "markets", ["tracked"])

    op.create_table(
        "quotes",
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("token_id", sa.Text, nullable=False),
        sa.Column("market_id", sa.Text, nullable=False),
        sa.Column("best_bid", sa.Numeric, nullable=True),
        sa.Column("best_bid_size", sa.Numeric, nullable=True),
        sa.Column("best_ask", sa.Numeric, nullable=True),
        sa.Column("best_ask_size", sa.Numeric, nullable=True),
        sa.Column("midpoint", sa.Numeric, nullable=True),
        sa.Column("spread", sa.Numeric, nullable=True),
    )

    # Convert to a hypertable AFTER the table exists; build the index AFTER conversion.
    op.execute("SELECT create_hypertable('quotes', 'time', if_not_exists => TRUE);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_quotes_token_time ON quotes (token_id, time DESC);"
    )


def downgrade() -> None:
    op.drop_table("quotes")  # drops the hypertable
    op.drop_table("markets")
