"""SQLAlchemy Core table definitions (the schema the store reads/writes).

Prices/sizes are unbounded ``NUMERIC`` (<-> Python ``Decimal`` via asyncpg) so no
float money math is possible. The actual DDL — including the TimescaleDB hypertable
conversion and the time-series index ordering — lives in the Alembic migration; this
metadata mirrors it (and is the autogenerate ``target_metadata``).
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

markets = sa.Table(
    "markets",
    metadata,
    sa.Column("market_id", sa.Text, primary_key=True),  # Gamma id
    sa.Column("condition_id", sa.Text, nullable=False, unique=True),
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
    sa.Index("ix_markets_tracked", "tracked"),
)

# Hypertable on ``time`` (see migration 0001_init). No PK / FK: TimescaleDB restricts
# unique constraints not covering the partition column, and an outbound FK on the hot
# insert path is costly — integrity is guaranteed by the scanner (markets upserted
# before their quotes are written).
quotes = sa.Table(
    "quotes",
    metadata,
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

sa.Index("ix_quotes_token_time", quotes.c.token_id, quotes.c.time.desc())
