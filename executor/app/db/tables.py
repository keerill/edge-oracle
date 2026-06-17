"""SQLAlchemy Core schema for the execution module (the audit spine + state).

Mirrors the advisor's ``quant/app/db/tables.py`` discipline: all money is unbounded ``NUMERIC``
(<-> ``Decimal``, no float), append-only where it's a log. The actual DDL lives in the Alembic
migration; this metadata mirrors it and is the autogenerate target.

NOTHING here ever stores a private key, seed phrase, raw approval token, or KMS credential —
only a HASH of the approval token is persisted. Keys live exclusively in the (later) KMS-backed
signer service.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

# Every intent the executor forms (append-only). The signed-able instruction + its binding hash.
exec_intents = sa.Table(
    "exec_intents",
    metadata,
    sa.Column("intent_id", sa.Text, primary_key=True),
    sa.Column("source_signal_id", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("side", sa.Text, nullable=False),
    sa.Column("chain_id", sa.Integer, nullable=False),
    sa.Column("market_id", sa.Text, nullable=False),
    sa.Column("condition_id", sa.Text, nullable=False),
    sa.Column("size", sa.Numeric, nullable=False),
    sa.Column("max_price", sa.Numeric, nullable=True),
    sa.Column("max_slippage", sa.Numeric, nullable=False),
    sa.Column("notional_usd", sa.Numeric, nullable=False),
    sa.Column("to_address", sa.Text, nullable=False),
    sa.Column("token_id", sa.Text, nullable=True),
    sa.Column("approve_spender", sa.Text, nullable=True),
    sa.Column("approve_amount", sa.Numeric, nullable=True),
    sa.Column("nonce", sa.Integer, nullable=False),
    sa.Column("intent_hash", sa.Text, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("expiry", sa.TIMESTAMP(timezone=True), nullable=False),
)

sa.Index("ix_exec_intents_created", exec_intents.c.created_at.desc())
sa.Index("ix_exec_intents_source", exec_intents.c.source_signal_id)

# The audit spine: one row per state transition, INSERT-ONLY (never updated).
exec_audit = sa.Table(
    "exec_audit",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("intent_id", sa.Text, nullable=False),
    # formed / breaker_rejected / pending_approval / approved / signed / submitted / confirmed / failed
    sa.Column("event", sa.Text, nullable=False),
    sa.Column("detail", sa.JSON, nullable=True),
    sa.Column("actor", sa.Text, nullable=True),  # 'system' or an approver identity
    sa.Column("tx_hash", sa.Text, nullable=True),
)

sa.Index("ix_exec_audit_intent_time", exec_audit.c.intent_id, exec_audit.c.time.desc())
sa.Index("ix_exec_audit_time", exec_audit.c.time.desc())

# Human approvals for above-threshold intents. We store only a HASH of the token, never the token.
exec_approvals = sa.Table(
    "exec_approvals",
    metadata,
    sa.Column("intent_id", sa.Text, primary_key=True),
    sa.Column("approval_token_hash", sa.Text, nullable=False),
    sa.Column("threshold_usd", sa.Numeric, nullable=False),
    sa.Column("approver", sa.Text, nullable=False),
    sa.Column("granted_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("consumed", sa.Boolean, nullable=False, server_default=sa.text("false")),
)

# Authoritative on-chain nonce counter, per (address, chain_id). Allocated under FOR UPDATE.
exec_nonces = sa.Table(
    "exec_nonces",
    metadata,
    sa.Column("address", sa.Text, primary_key=True),
    sa.Column("chain_id", sa.Integer, primary_key=True),
    sa.Column("next_nonce", sa.BigInteger, nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

# Allowlist: target contracts, approve spenders, withdrawal recipients. Read by breakers + signer.
exec_allowlist = sa.Table(
    "exec_allowlist",
    metadata,
    sa.Column("address", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),  # contract | spender | withdrawal
    sa.Column("label", sa.Text, nullable=True),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column("added_by", sa.Text, nullable=True),
    sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.PrimaryKeyConstraint("address", "kind", name="pk_exec_allowlist"),
)

# Durable, authoritative rolling-window counters (NOT executor memory — a restart must not reset
# them). One row per window bucket; the breaker reads the live bucket.
exec_breaker_counters = sa.Table(
    "exec_breaker_counters",
    metadata,
    sa.Column("window_start", sa.TIMESTAMP(timezone=True), primary_key=True),
    sa.Column("trade_count", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("notional_usd", sa.Numeric, nullable=False, server_default=sa.text("0")),
)
