"""The poller: discovery + scan loop. The only module wiring clients + transform +
store together. All decisions live in the pure transform; this stays thin.

Two cadences: snapshot every ``scan_interval_s``; refresh discovery every
``discovery_interval_s`` (less often). ``run_scan_once`` is the timing-free, fully
injectable test seam. Failures are isolated per token and per market so one bad book
never kills a tick, and one bad tick never kills the loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker
from app.db.tables import markets as markets_table
from app.ingestion import store, transform
from app.models.market import Market
from app.models.quote import QuoteSnapshot
from app.observability.logging import configure_logging
from app.observability.sentry import init_sentry
from app.polymarket.clob_client import ClobClient
from app.polymarket.gamma_client import GammaClient
from app.polymarket.http import make_http_client

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class ScanResult:
    markets: int
    quotes: int
    discovered: bool


async def discover_universe(gamma: GammaClient, settings: Settings) -> list[Market]:
    """Fetch + rank the tracked universe (top-N by liquidity, or allowlist)."""
    raw_markets = await gamma.list_active_markets(
        limit=settings.gamma_page_limit, order="liquidity", ascending=False
    )
    markets: list[Market] = []
    for raw in raw_markets:
        try:
            markets.append(transform.market_from_raw(raw))
        except ValueError as exc:
            logger.debug("skipping market %s during discovery: %s", raw.id, exc)
    return transform.rank_and_select(
        markets, top_n=settings.top_n, allowlist=settings.allowlist_ids
    )


async def _load_tracked(session: AsyncSession) -> list[Market]:
    """Reload the currently-tracked universe from the DB (no outcomes needed)."""
    rows = (
        await session.execute(
            sa.select(markets_table).where(markets_table.c.tracked.is_(True))
        )
    ).mappings().all()
    return [
        Market(
            market_id=r["market_id"],
            condition_id=r["condition_id"],
            question=r["question"],
            slug=r["slug"],
            category=r["category"],
            event_id=r["event_id"],
            yes_token_id=r["yes_token_id"],
            no_token_id=r["no_token_id"],
            enable_order_book=r["enable_order_book"],
            active=r["active"],
            closed=r["closed"],
            liquidity=r["liquidity"],
        )
        for r in rows
    ]


async def snapshot_market(
    clob: ClobClient, sem: asyncio.Semaphore, market: Market, at: datetime
) -> list[QuoteSnapshot]:
    """Snapshot both tokens of a market. Per-token isolation: a malformed/failed
    book for one token is logged and skipped; the other token still records."""
    snapshots: list[QuoteSnapshot] = []
    for token_id in (market.yes_token_id, market.no_token_id):
        try:
            async with sem:
                raw_book = await clob.get_book(token_id)
            book = transform.orderbook_from_raw(raw_book, token_id)
            snapshots.append(
                transform.quote_from_book(book, market_id=market.market_id, at=at)
            )
        except Exception as exc:  # noqa: BLE001 - poller must survive bad upstream data
            logger.warning(
                "skipping token %s of market %s: %r", token_id, market.market_id, exc
            )
    return snapshots


async def run_scan_once(
    gamma: GammaClient,
    clob: ClobClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    do_discovery: bool,
    now: Callable[[], datetime] = _utcnow,
) -> ScanResult:
    """One scan cycle (timing-free). Optionally refresh discovery, then snapshot the
    tracked universe and persist the tick. The injectable test seam."""
    at = now()

    if do_discovery:
        universe = await discover_universe(gamma, settings)
        async with sessionmaker() as session:
            await store.upsert_markets(session, universe)
            await store.set_untracked(session, {m.market_id for m in universe})
            await session.commit()
    else:
        async with sessionmaker() as session:
            universe = await _load_tracked(session)

    sem = asyncio.Semaphore(settings.max_concurrency)
    results = await asyncio.gather(
        *(snapshot_market(clob, sem, m, at) for m in universe),
        return_exceptions=True,
    )
    quotes: list[QuoteSnapshot] = []
    for r in results:
        if isinstance(r, Exception):  # per-market isolation (belt-and-suspenders)
            logger.warning("market snapshot task failed: %r", r)
            continue
        quotes.extend(r)

    async with sessionmaker() as session:
        n = await store.insert_quotes(session, quotes)
        await session.commit()

    return ScanResult(markets=len(universe), quotes=n, discovered=do_discovery)


async def run_poller(
    gamma: GammaClient,
    clob: ClobClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run the scan loop forever (or ``max_cycles`` times, for tests). Discovery
    fires on the first cycle and whenever ``discovery_interval_s`` has elapsed."""
    last_discovery_at: datetime | None = None
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        current = now()
        do_discovery = (
            last_discovery_at is None
            or (current - last_discovery_at).total_seconds() >= settings.discovery_interval_s
        )
        try:
            result = await run_scan_once(
                gamma, clob, sessionmaker, settings, do_discovery=do_discovery, now=now
            )
            if do_discovery:
                last_discovery_at = current
            logger.info(
                "scan complete: markets=%d quotes=%d discovered=%s",
                result.markets,
                result.quotes,
                result.discovered,
            )
        except Exception as exc:  # noqa: BLE001 - never let one tick kill the loop
            logger.exception("scan cycle failed: %r", exc)

        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        await sleep(settings.scan_interval_s)


async def run_poller_forever() -> None:
    """Entry point for the FastAPI lifespan toggle: build deps from settings + loop."""
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with make_http_client(settings) as http:
        gamma = GammaClient(http, settings)
        clob = ClobClient(http, settings)
        await run_poller(gamma, clob, sessionmaker, settings)


async def _run_once() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with make_http_client(settings) as http:
        gamma = GammaClient(http, settings)
        clob = ClobClient(http, settings)
        result = await run_scan_once(
            gamma, clob, sessionmaker, settings, do_discovery=True
        )
    logger.info(
        "one-shot scan: markets=%d quotes=%d", result.markets, result.quotes
    )


def main() -> None:
    """CLI: ``python -m app.ingestion.scanner`` (one cycle) or ``... loop`` (forever)."""
    configure_logging("quant.scanner")
    init_sentry("quant.scanner")
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "loop":
        asyncio.run(run_poller_forever())
    else:
        asyncio.run(_run_once())


if __name__ == "__main__":
    main()
