"""Persistence for the execution module — the audit spine, intents, approvals, allowlist, and
the authoritative nonce allocator. Async SQLAlchemy Core (asyncpg); ``Decimal`` <-> ``NUMERIC``
end-to-end (no float money). Mirrors ``quant/app/ingestion/store.py`` style.

The nonce allocator serializes concurrent intents with ``SELECT ... FOR UPDATE`` so two intents
never claim the same on-chain nonce (replay/duplicate protection starts here).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import (
    exec_allowlist,
    exec_approvals,
    exec_audit,
    exec_intents,
    exec_nonces,
)
from app.models.intent import Intent


async def insert_intent(session: AsyncSession, intent: Intent, intent_hash: str) -> None:
    """Append the formed intent + its binding hash (append-only)."""
    await session.execute(
        sa.insert(exec_intents).values(
            intent_id=intent.intent_id,
            source_signal_id=intent.source_signal_id,
            action=intent.action,
            side=intent.side,
            chain_id=intent.chain_id,
            market_id=intent.market_id,
            condition_id=intent.condition_id,
            size=intent.size,
            max_price=intent.max_price,
            max_slippage=intent.max_slippage,
            notional_usd=intent.notional_usd,
            to_address=intent.to_address,
            token_id=intent.token_id,
            approve_spender=intent.approve_spender,
            approve_amount=intent.approve_amount,
            nonce=intent.nonce,
            intent_hash=intent_hash,
            created_at=intent.created_at,
            expiry=intent.expiry,
        )
    )


async def load_intent(session: AsyncSession, intent_id: str) -> Intent | None:
    """Rebuild a stored ``Intent`` by id (the read counterpart to ``insert_intent``), so an
    approved-later flow can re-seal the SAME intent (identical hash) without re-forming it."""
    row = (
        await session.execute(
            sa.select(exec_intents).where(exec_intents.c.intent_id == intent_id)
        )
    ).mappings().first()
    if row is None:
        return None
    return Intent(
        intent_id=row["intent_id"],
        created_at=row["created_at"],
        expiry=row["expiry"],
        source_signal_id=row["source_signal_id"],
        action=row["action"],
        chain_id=row["chain_id"],
        market_id=row["market_id"],
        condition_id=row["condition_id"],
        side=row["side"],
        size=row["size"],
        max_price=row["max_price"],
        max_slippage=row["max_slippage"],
        notional_usd=row["notional_usd"],
        to_address=row["to_address"],
        token_id=row["token_id"],
        approve_spender=row["approve_spender"],
        approve_amount=row["approve_amount"],
        nonce=row["nonce"],
    )


async def append_audit(
    session: AsyncSession,
    *,
    intent_id: str,
    event: str,
    detail: dict[str, Any] | None = None,
    actor: str | None = None,
    tx_hash: str | None = None,
    time: datetime | None = None,
) -> None:
    """Insert one immutable audit row (state transition)."""
    values: dict[str, Any] = dict(
        intent_id=intent_id, event=event, detail=detail, actor=actor, tx_hash=tx_hash
    )
    if time is not None:
        values["time"] = time
    await session.execute(sa.insert(exec_audit).values(**values))


async def load_audit_trail(session: AsyncSession, intent_id: str) -> list[sa.Row]:
    """Oldest-first audit rows for one intent (forensics / status)."""
    result = await session.execute(
        sa.select(exec_audit)
        .where(exec_audit.c.intent_id == intent_id)
        .order_by(exec_audit.c.time.asc(), exec_audit.c.id.asc())
    )
    return list(result.all())


async def allocate_nonce(session: AsyncSession, address: str, chain_id: int) -> int:
    """Atomically allocate the next on-chain nonce for ``(address, chain_id)``.

    Ensures the counter row exists, then locks it ``FOR UPDATE`` so concurrent allocations
    serialize and never hand out the same nonce twice. Caller commits the surrounding txn."""
    await session.execute(
        sa.dialects.postgresql.insert(exec_nonces)
        .values(address=address, chain_id=chain_id, next_nonce=0)
        .on_conflict_do_nothing(index_elements=["address", "chain_id"])
    )
    row = (
        await session.execute(
            sa.select(exec_nonces.c.next_nonce)
            .where(
                (exec_nonces.c.address == address) & (exec_nonces.c.chain_id == chain_id)
            )
            .with_for_update()
        )
    ).one()
    allocated = int(row[0])
    await session.execute(
        sa.update(exec_nonces)
        .where((exec_nonces.c.address == address) & (exec_nonces.c.chain_id == chain_id))
        .values(next_nonce=allocated + 1, updated_at=sa.func.now())
    )
    return allocated


async def add_allowlist_entry(
    session: AsyncSession,
    *,
    address: str,
    kind: str,
    label: str | None = None,
    added_by: str | None = None,
) -> None:
    await session.execute(
        sa.dialects.postgresql.insert(exec_allowlist)
        .values(address=address, kind=kind, label=label, added_by=added_by)
        .on_conflict_do_update(
            constraint="pk_exec_allowlist", set_={"active": True, "label": label}
        )
    )


async def load_allowlist(session: AsyncSession, kind: str) -> frozenset[str]:
    """Active allowlisted addresses of a given kind (contract | spender | withdrawal)."""
    result = await session.execute(
        sa.select(exec_allowlist.c.address).where(
            (exec_allowlist.c.kind == kind) & (exec_allowlist.c.active.is_(True))
        )
    )
    return frozenset(r[0] for r in result.all())


async def insert_approval(
    session: AsyncSession,
    *,
    intent_id: str,
    approval_token_hash: str,
    threshold_usd: Decimal,
    approver: str,
    granted_at: datetime,
    expires_at: datetime,
) -> None:
    """Record a human approval (only the token HASH is stored, never the token)."""
    await session.execute(
        sa.insert(exec_approvals).values(
            intent_id=intent_id,
            approval_token_hash=approval_token_hash,
            threshold_usd=threshold_usd,
            approver=approver,
            granted_at=granted_at,
            expires_at=expires_at,
        )
    )
