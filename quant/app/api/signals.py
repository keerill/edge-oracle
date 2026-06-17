"""GET /signals and GET /signals/{id} — detected signals enriched with live sizing.

Each stored signal is joined with the latest quote for its market and run through the pure
``app.advisor.view.advise`` (which reuses the Kelly sizing math). Money fields serialize as
JSON strings (Decimal); the web Zod boundary parses them.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.advisor.ranking import rank_signals
from app.advisor.view import advise
from app.api.config import effective_config
from app.api.deps import get_app_settings, get_session
from app.config import Settings
from app.ingestion import store
from app.math.calibration import CalibrationParams, summarize
from app.models.advisor import AdvisedSignal
from app.models.market import Market
from app.models.quote import QuoteSnapshot
from app.models.signal import ArbSignal, Signal

router = APIRouter(prefix="/signals", tags=["signals"])


def _strategy_of(signal: Signal) -> str:
    """The strategy tag (ArbSignal has none of its own — it is the table default)."""
    if isinstance(signal, ArbSignal):
        return "set_arb"
    return signal.strategy


def make_signal_id(signal: Signal) -> str:
    """Synthesize a stable, round-trippable id from the natural key ``(strategy, market_id,
    time)``. The ``signals`` table has no surrogate PK; the 15s scan cadence makes a same-ms
    collision within one (strategy, market) effectively impossible."""
    epoch_ms = int(signal.time.timestamp() * 1000)
    return f"{_strategy_of(signal)}:{signal.market_id}:{epoch_ms}"


def _parse_id(raw: str) -> tuple[str, str, int]:
    """Split ``strategy:market_id:epoch_ms`` back into its parts (market_id may contain ':')."""
    try:
        strategy, rest = raw.split(":", 1)
        market_id, epoch_raw = rest.rsplit(":", 1)
        return strategy, market_id, int(epoch_raw)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail=f"malformed signal id: {raw!r}") from exc


async def effective_kelly_frac(session: AsyncSession, base_frac: Decimal) -> Decimal:
    """The live Kelly fraction: the user's ``base_frac``, shrunk (never raised) by the
    calibration journal when the model has proven overconfident. Falls back to ``base_frac``
    on an empty journal or when there's no high-confidence evidence yet."""
    records = await store.load_calibration(session)
    if not records:
        return base_frac
    adjusted = summarize(records, CalibrationParams(base_frac=base_frac)).kelly.adjusted_frac
    return adjusted if adjusted is not None else base_frac


def _enrich(
    signal: Signal,
    markets_by_id: dict[str, Market],
    quotes_by_token: dict[str, QuoteSnapshot],
    settings: Settings,
    bankroll: Decimal,
    frac: Decimal,
    cap: Decimal,
) -> AdvisedSignal:
    market = markets_by_id.get(signal.market_id)
    yes_quote = quotes_by_token.get(market.yes_token_id) if market else None
    no_quote = quotes_by_token.get(market.no_token_id) if market else None
    return advise(
        signal,
        signal_id=make_signal_id(signal),
        market_question=market.question if market else None,
        yes_quote=yes_quote,
        no_quote=no_quote,
        bankroll=bankroll,
        frac=frac,
        cap=cap,
        slippage=settings.arb_slippage,
        gas=settings.arb_gas,
        model_error_margin=settings.model_error_margin,
    )


def _bankroll(default: Decimal, override: str | None) -> Decimal:
    if override is None:
        return default
    try:
        value = Decimal(override)
    except InvalidOperation as exc:
        raise HTTPException(status_code=422, detail=f"bankroll must be numeric, got {override!r}") from exc
    if value < 0:
        raise HTTPException(status_code=422, detail="bankroll must be >= 0")
    return value


def _min_net_edge(override: str | None) -> Decimal | None:
    if override is None:
        return None
    try:
        return Decimal(override)
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=422, detail=f"min_net_edge must be numeric, got {override!r}"
        ) from exc


@router.get("", response_model=list[AdvisedSignal])
async def list_signals(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    limit: int = Query(100, ge=1, le=500),
    strategy: str | None = Query(None),
    bankroll: str | None = Query(None, description="override the advisor bankroll (USD)"),
    sort: str = Query("net_edge", pattern="^(net_edge|safety)$"),
    safe_only: bool = Query(False, description="keep only risk-free arb + gate-passing bets"),
    min_net_edge: str | None = Query(None, description="drop signals below this net edge"),
) -> list[AdvisedSignal]:
    """Current signals, enriched with sizing. ``sort=net_edge`` (default, descending) or
    ``sort=safety`` (risk-free arb first, then gate-passing directional, then the rest).
    ``safe_only`` and ``min_net_edge`` filter the list."""
    cfg = await effective_config(session, settings)
    bank = _bankroll(cfg.bankroll, bankroll)
    floor = _min_net_edge(min_net_edge)
    signals = await store.load_signals(session, strategy=strategy, limit=limit)
    markets = await store.load_tracked_markets(session)
    markets_by_id = {m.market_id: m for m in markets}
    token_ids = [tid for m in markets for tid in (m.yes_token_id, m.no_token_id)]
    quotes_by_token = await store.load_latest_quotes(session, token_ids=token_ids or None)
    frac = await effective_kelly_frac(session, cfg.kelly_frac)

    advised = [
        _enrich(s, markets_by_id, quotes_by_token, settings, bank, frac, cfg.kelly_cap)
        for s in signals
    ]
    return rank_signals(advised, sort=sort, safe_only=safe_only, min_net_edge=floor)


@router.get("/{signal_id}", response_model=AdvisedSignal)
async def get_signal(
    signal_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    bankroll: str | None = Query(None),
) -> AdvisedSignal:
    """One signal's full sizing breakdown + cost-gate components."""
    cfg = await effective_config(session, settings)
    bank = _bankroll(cfg.bankroll, bankroll)
    strategy, market_id, epoch_ms = _parse_id(signal_id)
    candidates = await store.load_signals(session, strategy=strategy, limit=500)
    match = next(
        (
            s
            for s in candidates
            if s.market_id == market_id and int(s.time.timestamp() * 1000) == epoch_ms
        ),
        None,
    )
    if match is None:
        raise HTTPException(status_code=404, detail=f"signal not found: {signal_id}")

    markets = await store.load_tracked_markets(session)
    markets_by_id = {m.market_id: m for m in markets}
    market = markets_by_id.get(market_id)
    token_ids = [market.yes_token_id, market.no_token_id] if market else None
    quotes_by_token = await store.load_latest_quotes(session, token_ids=token_ids)
    frac = await effective_kelly_frac(session, cfg.kelly_frac)
    return _enrich(match, markets_by_id, quotes_by_token, settings, bank, frac, cfg.kelly_cap)
