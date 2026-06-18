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

# Trade prints — one row per executed trade on a tracked market. A hypertable on ``time`` like
# ``quotes`` (no PK — TimescaleDB restricts unique constraints not covering the partition column).
# ``price``/``size`` are unbounded NUMERIC (Decimal end-to-end). ``trade_id`` is the fill's tx hash
# (not guaranteed unique per row — a tx can carry several fills — so it's a plain column, not a key).
trades = sa.Table(
    "trades",
    metadata,
    sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("token_id", sa.Text, nullable=False),
    sa.Column("market_id", sa.Text, nullable=False),
    sa.Column("price", sa.Numeric, nullable=False),
    sa.Column("size", sa.Numeric, nullable=False),
    sa.Column("taker_side", sa.Text, nullable=True),
    sa.Column("trade_id", sa.Text, nullable=False),
)

sa.Index("ix_trades_token_time", trades.c.token_id, trades.c.time.desc())

# Detected signals — one row per flagged market per scan, across strategies. A *regular*
# table, not a hypertable: signals are sparse and append-only, so time-chunking buys
# little. No PK (mirrors ``quotes``). ``strategy`` tags the producer and ``kind`` is its
# per-strategy subtype/side. The set-arb columns and the price-signal columns are mutually
# exclusive per row (each strategy fills only its own), so all are nullable. All money is
# unbounded NUMERIC. Indexed by time, by (market_id, time), and by (strategy, time).
signals = sa.Table(
    "signals",
    metadata,
    sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("market_id", sa.Text, nullable=False),
    sa.Column("condition_id", sa.Text, nullable=False),
    sa.Column("strategy", sa.Text, nullable=False, server_default=sa.text("'set_arb'")),
    sa.Column("kind", sa.Text, nullable=False),  # per-strategy subtype/side
    # --- set-arb columns (only ``set_arb`` rows populate these) ---
    sa.Column("yes_price", sa.Numeric, nullable=True),
    sa.Column("no_price", sa.Numeric, nullable=True),
    sa.Column("set_size", sa.Numeric, nullable=True),
    sa.Column("gross_edge", sa.Numeric, nullable=True),
    sa.Column("estimated_costs", sa.Numeric, nullable=True),
    sa.Column("net_edge", sa.Numeric, nullable=True),
    sa.Column("hypothetical_pnl", sa.Numeric, nullable=True),
    # --- price-signal columns (favourite-longshot / extreme-correction) ---
    sa.Column("price", sa.Numeric, nullable=True),  # the market price m (the input)
    sa.Column("edge_score", sa.Numeric, nullable=True),  # favourite-longshot, in [0, 1]
    sa.Column("fair_value", sa.Numeric, nullable=True),  # extreme-correction corrected prob
)

sa.Index("ix_signals_market_time", signals.c.market_id, signals.c.time.desc())
sa.Index("ix_signals_time", signals.c.time.desc())
sa.Index("ix_signals_strategy_time", signals.c.strategy, signals.c.time.desc())

# Calibration journal — one resolved prediction per row: the probability we claimed
# (``estimate``), the market price we saw (``price``), and the realized ``outcome`` (0/1),
# tagged by the producing ``strategy``. A *regular* table like ``signals`` (even sparser:
# one row per resolution), no PK. ``estimate``/``price`` are unbounded NUMERIC (Decimal);
# ``outcome`` is a label not money, so SmallInteger + a CHECK. ``strategy`` is always
# supplied (NOT NULL, no server_default — a default would silently mislabel rows).
calibration = sa.Table(
    "calibration",
    metadata,
    sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("market_id", sa.Text, nullable=False),
    sa.Column("condition_id", sa.Text, nullable=False),
    sa.Column("strategy", sa.Text, nullable=False),
    sa.Column("estimate", sa.Numeric, nullable=False),  # claimed probability p
    sa.Column("price", sa.Numeric, nullable=False),  # market YES price m at the time
    sa.Column("outcome", sa.SmallInteger, nullable=False),  # 1 = resolved YES, 0 = NO
    sa.CheckConstraint("outcome IN (0, 1)", name="ck_calibration_outcome"),
)

sa.Index("ix_calibration_time", calibration.c.time.desc())
sa.Index("ix_calibration_strategy_time", calibration.c.strategy, calibration.c.time.desc())

# Personal sizing / risk preferences — a single-row table keyed by a constant ``id`` (default
# 'default'), so the dashboard's bankroll & risk knobs persist across restarts and are applied
# server-side uniformly. All money/fractions are unbounded NUMERIC (Decimal). ``updated_at`` is
# refreshed on every upsert.
user_config = sa.Table(
    "user_config",
    metadata,
    sa.Column("id", sa.Text, primary_key=True, server_default=sa.text("'default'")),
    sa.Column("bankroll", sa.Numeric, nullable=False),
    sa.Column("kelly_frac", sa.Numeric, nullable=False),
    sa.Column("kelly_cap", sa.Numeric, nullable=False),
    sa.Column("corr_cap_frac", sa.Numeric, nullable=False),
    sa.Column("risk_threshold", sa.Numeric, nullable=False),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

# Portfolio positions — one row per bet the operator placed (manually on Polymarket). A regular
# table with a generated text PK. ``entry_price``/``stake_usd``/``shares``/``pnl`` are unbounded
# NUMERIC (Decimal). ``outcome``/``pnl``/``resolved_at`` stay NULL until the market resolves and
# the position is settled (``status='closed'``). Indexed by status and by market for settlement.
positions = sa.Table(
    "positions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("market_id", sa.Text, nullable=False),
    sa.Column("condition_id", sa.Text, nullable=False),
    sa.Column("strategy", sa.Text, nullable=False),
    sa.Column("side", sa.Text, nullable=False),  # yes / no / set
    sa.Column("entry_price", sa.Numeric, nullable=False),  # all-in price paid per share
    sa.Column("stake_usd", sa.Numeric, nullable=False),
    sa.Column("shares", sa.Numeric, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'open'")),
    sa.Column("outcome", sa.SmallInteger, nullable=True),
    sa.Column("pnl", sa.Numeric, nullable=True),
    sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("signal_id", sa.Text, nullable=True),
    sa.CheckConstraint("outcome IS NULL OR outcome IN (0, 1)", name="ck_positions_outcome"),
)

sa.Index("ix_positions_status", positions.c.status)
sa.Index("ix_positions_condition", positions.c.condition_id)

# Paper-trading journal — one row per advisory recommendation the system would have placed,
# logged automatically at advice time (no money, no execution) so the strategy can be scored
# against real outcomes. The no-money sibling of ``positions``: same shape, but auto-captured
# rather than operator-entered. ``p``/``p_lo`` (claimed prob + CI lower bound) populate only
# for directional rows; ``outcome``/``realized_pnl``/``resolved_at`` stay NULL until settlement.
# ``status`` is open|closed|expired. All money is unbounded NUMERIC (Decimal). Indexed by
# status and by condition for settlement.
paper_trades = sa.Table(
    "paper_trades",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("advised_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("strategy", sa.Text, nullable=False),
    sa.Column("market_id", sa.Text, nullable=False),
    sa.Column("condition_id", sa.Text, nullable=False),
    sa.Column("side", sa.Text, nullable=False),  # yes / no / set
    sa.Column("advised_price", sa.Numeric, nullable=False),  # all-in price paid per share
    sa.Column("stake_usd", sa.Numeric, nullable=False),
    sa.Column("shares", sa.Numeric, nullable=False),
    sa.Column("edge", sa.Numeric, nullable=False),
    sa.Column("p", sa.Numeric, nullable=True),  # claimed probability (directional only)
    sa.Column("p_lo", sa.Numeric, nullable=True),  # CI lower bound (directional only)
    sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'open'")),
    sa.Column("outcome", sa.SmallInteger, nullable=True),
    sa.Column("realized_pnl", sa.Numeric, nullable=True),
    sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("signal_id", sa.Text, nullable=True),
    # Set-arb fill re-check verdict (NULL for directional / when the check is disabled).
    sa.Column("fill_checked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("fill_ok", sa.Boolean, nullable=True),
    sa.Column("fill_latency_s", sa.Numeric, nullable=True),
    sa.Column("fill_reason", sa.Text, nullable=True),
    sa.Column("rechecked_net_edge", sa.Numeric, nullable=True),  # verified fillable edge
    sa.CheckConstraint("outcome IS NULL OR outcome IN (0, 1)", name="ck_paper_trades_outcome"),
)

sa.Index("ix_paper_trades_status", paper_trades.c.status)
sa.Index("ix_paper_trades_condition", paper_trades.c.condition_id)
