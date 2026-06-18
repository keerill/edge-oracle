"""Pure paper-trading performance math — settled paper trades -> a scorecard.

No I/O, no clock, no ``Settings``. Reuses the backtest's metric primitives (``total_return``,
``max_drawdown``, ``hit_rate``-style win counting, ``sharpe_like``) so the live paper record
and the historical replay never disagree on how a number is computed. Pinned by hand-computed
unit tests in ``tests/test_paper_performance.py``.

A paper trade "won" when its realized P&L is positive. Per-bet return is ``realized_pnl /
stake_usd`` (set-arb uses a $1 stake basis). The equity curve is the opening bankroll plus the
cumulative realized P&L, sampled at each settlement in resolution order.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.math.backtest import max_drawdown, sharpe_like, total_return
from app.models.backtest import EquityPoint
from app.models.paper_performance import PaperPerformance, PaperStrategyPerf
from app.models.paper_trade import PaperTrade

ZERO = Decimal(0)


def _settled_sorted(trades: Sequence[PaperTrade]) -> list[PaperTrade]:
    """Settled trades (realized P&L known) in resolution order (advice time as a tiebreak)."""
    settled = [t for t in trades if t.realized_pnl is not None]
    return sorted(settled, key=lambda t: (t.resolved_at or t.advised_at, t.advised_at))


def _per_bet_return(t: PaperTrade) -> Decimal | None:
    if t.stake_usd <= ZERO or t.realized_pnl is None:
        return None
    return t.realized_pnl / t.stake_usd


def _strategy_perf(strategy: str, trades: Sequence[PaperTrade]) -> PaperStrategyPerf:
    n = len(trades)
    wins = sum(1 for t in trades if (t.realized_pnl or ZERO) > ZERO)
    total_pnl = sum((t.realized_pnl or ZERO for t in trades), ZERO)
    returns = [r for r in (_per_bet_return(t) for t in trades) if r is not None]
    avg_return = (sum(returns, ZERO) / Decimal(len(returns))) if returns else None
    return PaperStrategyPerf(
        strategy=strategy,
        n=n,
        wins=wins,
        hit_rate=(Decimal(wins) / Decimal(n)) if n else None,
        total_pnl=total_pnl,
        avg_return=avg_return,
        sharpe_like=sharpe_like(returns),
    )


def summarize_paper_trades(
    trades: Sequence[PaperTrade],
    *,
    initial_bankroll: Decimal,
    n_open: int = 0,
) -> PaperPerformance:
    """Score the settled paper trades into a performance report. ``n_open`` is the count of
    still-open (unsettled) paper trades, surfaced for context. ``initial_bankroll`` must be > 0.
    """
    settled = _settled_sorted(trades)
    total_pnl = sum((t.realized_pnl or ZERO for t in settled), ZERO)
    final = initial_bankroll + total_pnl

    equity = initial_bankroll
    curve: list[EquityPoint] = []
    for t in settled:
        equity += t.realized_pnl or ZERO
        curve.append(EquityPoint(time=t.resolved_at or t.advised_at, equity=equity))

    equity_series = [initial_bankroll, *(p.equity for p in curve)]
    wins = sum(1 for t in settled if (t.realized_pnl or ZERO) > ZERO)
    returns = [r for r in (_per_bet_return(t) for t in settled) if r is not None]

    by_strategy: dict[str, list[PaperTrade]] = {}
    for t in settled:
        by_strategy.setdefault(t.strategy, []).append(t)
    per_strategy = {s: _strategy_perf(s, ts) for s, ts in by_strategy.items()}

    return PaperPerformance(
        initial_bankroll=initial_bankroll,
        final_bankroll=final,
        total_pnl=total_pnl,
        total_return=total_return(initial_bankroll, final),
        hit_rate=(Decimal(wins) / Decimal(len(settled))) if settled else None,
        max_drawdown=max_drawdown(equity_series),
        sharpe_like=sharpe_like(returns),
        n_closed=len(settled),
        n_open=n_open,
        per_strategy=per_strategy,
        equity_curve=tuple(curve),
        arb_fill_assumed="set_arb" in per_strategy,
    )
