"""add set-arb fill re-check columns to paper_trades

Revision ID: 0009_paper_trades_fill_check
Revises: 0008_paper_trades
Create Date: 2026-06-18

The fill-check is the gating item before trusting set-arb paper P&L: at capture time the
dislocation is re-priced on a fresh book, so the arb track is only trusted when the edge
survived the latency gap. These nullable columns carry that verdict (NULL for directional
rows / when the check is disabled). ``rechecked_net_edge`` is the verified fillable edge that
arb P&L settles on. Mirrors ``db/tables.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_paper_trades_fill_check"
down_revision: str | None = "0008_paper_trades"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "paper_trades", sa.Column("fill_checked_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("paper_trades", sa.Column("fill_ok", sa.Boolean, nullable=True))
    op.add_column("paper_trades", sa.Column("fill_latency_s", sa.Numeric, nullable=True))
    op.add_column("paper_trades", sa.Column("fill_reason", sa.Text, nullable=True))
    op.add_column("paper_trades", sa.Column("rechecked_net_edge", sa.Numeric, nullable=True))


def downgrade() -> None:
    op.drop_column("paper_trades", "rechecked_net_edge")
    op.drop_column("paper_trades", "fill_reason")
    op.drop_column("paper_trades", "fill_latency_s")
    op.drop_column("paper_trades", "fill_ok")
    op.drop_column("paper_trades", "fill_checked_at")
