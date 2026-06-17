"""exec init — intents, audit spine, approvals, nonces, allowlist, breaker counters

Revision ID: 0001_exec_init
Revises:
Create Date: 2026-06-17

The execution module's own schema (separate db from the advisor). All money is unbounded
NUMERIC (<-> Decimal). Mirrors db/tables.py. No keys/secrets are ever stored — only a hash of
the approval token.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_exec_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exec_intents",
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
    op.execute("CREATE INDEX IF NOT EXISTS ix_exec_intents_created ON exec_intents (created_at DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exec_intents_source ON exec_intents (source_signal_id);")

    op.create_table(
        "exec_audit",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("intent_id", sa.Text, nullable=False),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("detail", sa.JSON, nullable=True),
        sa.Column("actor", sa.Text, nullable=True),
        sa.Column("tx_hash", sa.Text, nullable=True),
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_exec_audit_intent_time ON exec_audit (intent_id, time DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exec_audit_time ON exec_audit (time DESC);")

    op.create_table(
        "exec_approvals",
        sa.Column("intent_id", sa.Text, primary_key=True),
        sa.Column("approval_token_hash", sa.Text, nullable=False),
        sa.Column("threshold_usd", sa.Numeric, nullable=False),
        sa.Column("approver", sa.Text, nullable=False),
        sa.Column("granted_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "exec_nonces",
        sa.Column("address", sa.Text, nullable=False),
        sa.Column("chain_id", sa.Integer, nullable=False),
        sa.Column("next_nonce", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("address", "chain_id"),
    )

    op.create_table(
        "exec_allowlist",
        sa.Column("address", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("added_by", sa.Text, nullable=True),
        sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("address", "kind", name="pk_exec_allowlist"),
    )

    op.create_table(
        "exec_breaker_counters",
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("trade_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("notional_usd", sa.Numeric, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_table("exec_breaker_counters")
    op.drop_table("exec_allowlist")
    op.drop_table("exec_nonces")
    op.drop_table("exec_approvals")
    op.drop_table("exec_audit")
    op.drop_table("exec_intents")
