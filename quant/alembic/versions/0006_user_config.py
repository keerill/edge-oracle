"""add user_config single-row table

Revision ID: 0006_user_config
Revises: 0005_trades
Create Date: 2026-06-17

A single-row table (keyed by a constant ``id`` defaulting to 'default') holding the
operator's personal bankroll & risk-appetite knobs, so sizing is personal and survives
restarts. Mirrors ``db/tables.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_user_config"
down_revision: str | None = "0005_trades"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_config",
        sa.Column("id", sa.Text, primary_key=True, server_default=sa.text("'default'")),
        sa.Column("bankroll", sa.Numeric, nullable=False),
        sa.Column("kelly_frac", sa.Numeric, nullable=False),
        sa.Column("kelly_cap", sa.Numeric, nullable=False),
        sa.Column("corr_cap_frac", sa.Numeric, nullable=False),
        sa.Column("risk_threshold", sa.Numeric, nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("user_config")
