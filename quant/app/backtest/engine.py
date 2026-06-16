"""The backtest engine: replay stored quotes into sized, resolved bets and report.

A thin I/O adapter around the pure math (``app.math.backtest``) — decisions live in the
math; this layer just reads the stored ``quotes``/``markets``, turns each tick into the
existing signal evaluations (``extreme_correction`` + ``set_arb``), and hands the resulting
``BetCandidate``s to ``simulate``. Market outcomes are an **explicit input**
(``resolutions``) because resolution ingestion is a later slice.

``build_candidates`` is pure over plain quotes/markets/resolutions (the testable seam);
``run_backtest_once`` adds only the DB read. No look-ahead: each candidate is built from a
single tick's quote, entered at that tick's time, and resolved only at ``resolve_time``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.engine import get_sessionmaker
from app.ingestion import store
from app.math.arb import ArbParams, evaluate_market
from app.math.backtest import simulate_with_distribution
from app.math.correction import CorrectionParams, evaluate_extreme_correction
from app.models.backtest import BacktestParams, BacktestResult, BetCandidate, MarketResolution
from app.models.book import BookLevel, OrderBook
from app.models.market import Market
from app.models.quote import QuoteSnapshot
from app.models.signal import ArbSignal, ExtremeCorrectionSignal

logger = logging.getLogger(__name__)

ZERO = Decimal(0)
ONE = Decimal(1)


def _backtest_params(settings: Settings) -> BacktestParams:
    """Map the EDGE_-prefixed knobs onto the pure BacktestParams (keeps the math pure)."""
    return BacktestParams(
        initial_bankroll=settings.backtest_initial_bankroll,
        frac=settings.kelly_frac,
        cap=settings.kelly_cap,
        corr_cap_frac=settings.corr_cap_frac,
        model_error_margin=settings.model_error_margin,
        mc_sigma=settings.mc_sigma,
        mc_sims=settings.mc_sims,
        mc_seed=settings.mc_seed,
    )


def _arb_params(settings: Settings) -> ArbParams:
    return ArbParams(
        set_size=settings.arb_set_size,
        gas=settings.arb_gas,
        slippage=settings.arb_slippage,
        min_net_edge=settings.arb_min_net_edge,
    )


def _book_from_quote(q: QuoteSnapshot) -> OrderBook:
    """Reconstruct a single-level order book from a stored top-of-book snapshot (all the
    depth history we keep). A side with no price/size is left empty."""
    bids = (
        (BookLevel(price=q.best_bid, size=q.best_bid_size),)
        if q.best_bid is not None and q.best_bid_size is not None
        else ()
    )
    asks = (
        (BookLevel(price=q.best_ask, size=q.best_ask_size),)
        if q.best_ask is not None and q.best_ask_size is not None
        else ()
    )
    return OrderBook(token_id=q.token_id, bids=bids, asks=asks)


def _directional_candidate(
    sig: ExtremeCorrectionSignal,
    market: Market,
    yes_q: QuoteSnapshot,
    no_q: QuoteSnapshot,
    res: MarketResolution,
    tag: str,
    model_error_margin: Decimal,
    slippage: Decimal,
    gas: Decimal,
) -> BetCandidate | None:
    """Turn a correction signal into a directional bet on the side it favours.

    ``fair_value > price`` means the market underprices YES -> buy YES; otherwise buy NO.
    Sizing uses the *side token's* own midpoint + half-spread (the price you pay into),
    and ``p_side`` = P(that side wins). ``None`` if the side's book has no usable price.
    """
    p_yes = sig.fair_value
    side = "yes" if sig.fair_value > sig.price else "no"
    side_q = yes_q if side == "yes" else no_q
    p_side = p_yes if side == "yes" else ONE - p_yes
    if side_q.midpoint is None or side_q.spread is None:
        return None
    return BetCandidate(
        entry_time=yes_q.time,
        resolve_time=res.resolve_time,
        market_id=market.market_id,
        condition_id=market.condition_id,
        strategy="extreme_correction",
        tag=tag,
        kind="directional",
        side=side,
        m_side=side_q.midpoint,
        p_yes=p_yes,
        p_side=p_side,
        p_lo_side=p_side - model_error_margin,
        half_spread=side_q.spread / Decimal(2),
        slippage=slippage,
        gas=gas,
    )


def _arb_candidate(sig: ArbSignal, market: Market, res: MarketResolution, tag: str) -> BetCandidate:
    """Turn a set-arb signal into a risk-free bet. Capital is the set cost: the two ask
    VWAPs for a LONG set, or $1/set to mint a SHORT set."""
    capital = (
        (sig.yes_price + sig.no_price) * sig.set_size
        if sig.kind == "long_set"
        else sig.set_size * ONE
    )
    return BetCandidate(
        entry_time=sig.time,
        resolve_time=res.resolve_time,
        market_id=market.market_id,
        condition_id=market.condition_id,
        strategy="set_arb",
        tag=tag,
        kind="arb",
        locked_net_edge=sig.net_edge,
        set_size=sig.set_size,
        capital=capital,
    )


def build_candidates(
    quotes: Sequence[QuoteSnapshot],
    markets: Sequence[Market],
    resolutions: Mapping[str, MarketResolution],
    *,
    corr_params: CorrectionParams | None = None,
    arb_params: ArbParams | None = None,
    model_error_margin: Decimal = Decimal("0.05"),
    slippage: Decimal = Decimal("0.01"),
    gas: Decimal = Decimal("0.01"),
) -> list[BetCandidate]:
    """Replay ``quotes`` into entry decisions, time-ordered, with no look-ahead.

    For each market with a known resolution, walk the ticks where both tokens quoted and
    emit at most one ``extreme_correction`` and one ``set_arb`` candidate — the first
    qualifying tick of each. Every candidate is built from that single tick's data and
    entered at that tick's time; the outcome is applied only later, at ``resolve_time``.
    """
    corr_params = corr_params or CorrectionParams()
    arb_params = arb_params or ArbParams()

    by_token: dict[str, dict] = {}
    for q in quotes:
        by_token.setdefault(q.token_id, {})[q.time] = q

    candidates: list[BetCandidate] = []
    for m in markets:
        res = resolutions.get(m.condition_id)
        if res is None:
            continue  # no outcome -> can't compute P&L
        yes_q = by_token.get(m.yes_token_id, {})
        no_q = by_token.get(m.no_token_id, {})
        tag = m.category or m.condition_id  # one macro theme = one bet (correlation cap)
        corr_done = arb_done = False
        for t in sorted(set(yes_q) & set(no_q)):
            if t >= res.resolve_time:
                continue  # never enter at/after resolution
            yq, nq = yes_q[t], no_q[t]

            if not corr_done and yq.midpoint is not None:
                sig = evaluate_extreme_correction(
                    yq.midpoint, corr_params, market_id=m.market_id, condition_id=m.condition_id, at=t
                )
                if sig is not None:
                    cand = _directional_candidate(
                        sig, m, yq, nq, res, tag, model_error_margin, slippage, gas
                    )
                    if cand is not None:
                        candidates.append(cand)
                        corr_done = True

            if not arb_done:
                sig = evaluate_market(
                    _book_from_quote(yq),
                    _book_from_quote(nq),
                    arb_params,
                    market_id=m.market_id,
                    condition_id=m.condition_id,
                    at=t,
                )
                if sig is not None:
                    candidates.append(_arb_candidate(sig, m, res, tag))
                    arb_done = True
    return candidates


async def run_backtest_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    resolutions: Mapping[str, MarketResolution],
) -> BacktestResult:
    """Load the stored universe + quotes, build candidates, and run the simulation.

    Outcomes are supplied via ``resolutions`` (condition_id -> outcome + resolve_time);
    only resolved markets contribute bets.
    """
    async with sessionmaker() as session:
        markets = await store.load_tracked_markets(session)
        quotes = await store.load_quotes(session)

    candidates = build_candidates(
        quotes,
        markets,
        resolutions,
        arb_params=_arb_params(settings),
        model_error_margin=settings.model_error_margin,
        slippage=settings.arb_slippage,
        gas=settings.arb_gas,
    )
    outcomes = {cid: r.outcome for cid, r in resolutions.items()}
    # Offload the synchronous compute: monte_carlo re-runs simulate mc_sims times, which would
    # otherwise block the event loop on a request thread.
    return await run_in_threadpool(
        simulate_with_distribution, candidates, outcomes, _backtest_params(settings)
    )


def _load_resolutions(path: str) -> dict[str, MarketResolution]:
    """Parse a JSON resolutions file: ``{condition_id: {outcome, resolve_time}}`` (ISO
    timestamps). Validated at the boundary via the Pydantic model (untrusted input)."""
    raw = json.loads(open(path).read())
    return {cid: MarketResolution.model_validate(v) for cid, v in raw.items()}


async def _run(resolutions: dict[str, MarketResolution]) -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    result = await run_backtest_once(sessionmaker, settings, resolutions)
    logger.info(
        "backtest: bets=%d final=%s return=%s max_dd=%s sharpe=%s",
        result.n_bets,
        result.final_bankroll,
        result.total_return,
        result.max_drawdown,
        result.sharpe_like,
    )
    for strat, bd in result.per_strategy.items():
        logger.info("  %s: n=%d pnl=%s return=%s", strat, bd.n, bd.total_pnl, bd.total_return)


def main() -> None:
    """CLI: ``python -m app.backtest.engine <resolutions.json>``.

    The resolutions file is required — without market outcomes there is nothing to score
    (resolution ingestion is a future slice). Run with no argument to confirm wiring (it
    logs an empty result and the missing-feed warning)."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    if len(sys.argv) > 1:
        resolutions = _load_resolutions(sys.argv[1])
    else:
        logger.warning("no resolutions file given; backtest needs a market-outcome feed")
        resolutions = {}
    asyncio.run(_run(resolutions))


if __name__ == "__main__":
    main()
