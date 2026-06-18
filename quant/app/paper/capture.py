"""Pure capture logic: an enriched ``AdvisedSignal`` -> the ``PaperTrade`` we'd have placed.

No I/O, no clock — the engine supplies the loaded signals and the set of already-open keys.
We log a paper trade only for an *actionable* recommendation, mirroring what a human acting
on the advisor would actually do:

  * ``extreme_correction`` (directional): the cost gate passed AND a positive Kelly stake was
    recommended. The fill is the all-in ask (``economics.ask``), so the recorded P&L later
    matches the backtest's realized P&L. ``p``/``p_lo`` carry the claimed prob + CI lower bound.
  * ``set_arb``: a positive locked ``net_edge``. Risk-free and outcome-independent, so it is
    sized per **one set** ($1 capital basis, ``set_size = 1`` — the system's fixed arb size) and
    its realized P&L at settlement is just the locked edge; ``p``/``p_lo`` stay ``None``.
  * ``favourite_longshot``: never — a display-only heuristic with no money edge.

Dedup is by ``(strategy, condition_id)``: at most one open paper trade per strategy per market
at a time, so a signal that re-fires every scan cycle is logged once, not every 15s.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.models.advisor import AdvisedSignal
from app.models.paper_trade import PaperTrade

ZERO = Decimal(0)
ONE = Decimal(1)


def paper_trade_from_advice(advised: AdvisedSignal) -> PaperTrade | None:
    """Build the ``PaperTrade`` for an actionable recommendation, or ``None`` if we wouldn't
    take it. ``advised.id`` (``strategy:market_id:epoch_ms``) is the stable, unique row id."""
    if advised.strategy == "extreme_correction":
        if not advised.gate_passed or advised.recommended_size_usd <= ZERO:
            return None
        ask = advised.economics.ask if advised.economics else None
        if ask is None or ask <= ZERO:
            return None
        stake = advised.recommended_size_usd
        return PaperTrade(
            id=advised.id,
            advised_at=advised.time,
            strategy="extreme_correction",
            market_id=advised.market_id,
            condition_id=advised.condition_id,
            side="yes" if advised.kind == "buy_yes" else "no",
            advised_price=ask,
            stake_usd=stake,
            shares=stake / ask,
            edge=advised.net_edge,
            p=advised.p,
            p_lo=advised.gate.p_lo if advised.gate else None,
            signal_id=advised.id,
        )
    if advised.strategy == "set_arb":
        if advised.net_edge <= ZERO:
            return None
        return PaperTrade(
            id=advised.id,
            advised_at=advised.time,
            strategy="set_arb",
            market_id=advised.market_id,
            condition_id=advised.condition_id,
            side="set",
            advised_price=advised.market_price,  # informational: the set cost
            stake_usd=ONE,  # one set, $1 capital basis (system's fixed arb size)
            shares=ONE,
            edge=advised.net_edge,
            signal_id=advised.id,
        )
    return None  # favourite_longshot: display-only, never paper-traded


def select_new_paper_trades(
    advised: Iterable[AdvisedSignal],
    *,
    already_open: set[tuple[str, str]],
) -> list[PaperTrade]:
    """Pick the paper trades to log this cycle: the newest actionable recommendation per
    ``(strategy, condition_id)`` that has no open paper trade yet. ``already_open`` is the set
    of ``(strategy, condition_id)`` keys with a currently-open paper trade."""
    candidates = sorted(advised, key=lambda a: a.time, reverse=True)  # newest first
    seen: set[tuple[str, str]] = set()
    out: list[PaperTrade] = []
    for a in candidates:
        pt = paper_trade_from_advice(a)
        if pt is None:
            continue
        key = (pt.strategy, pt.condition_id)
        if key in already_open or key in seen:
            continue
        seen.add(key)
        out.append(pt)
    return out
