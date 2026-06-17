"""Pure per-bet profit / EV math — the dollar-denominated view a human acts on.

Where :mod:`app.math.bet_sizing` decides *how much* to stake, this module answers "what
do I make or lose on that stake": the dollar profit if the bet wins, the dollar loss if
it loses, the expected value, and the probability of a loss. It is the advisor-facing
companion to :func:`app.math.backtest.realized_pnl` — the win/loss payoff here is exactly
the payoff the backtest realizes, so "expected earnings" on the dashboard and realized P&L
in the replay never disagree.

Everything is pure (``Decimal`` in, ``Decimal`` out — no I/O, no clock, no ``Settings``)
and pinned by hand-computed unit tests in ``tests/test_profit.py``.

Conventions (see CLAUDE.md money-math rules):
  * ``ask`` is the **all-in price you pay** per share: ``m + half_spread + slippage + gas``
    (the gate ``threshold``), so the cost basis matches the backtest fill. Must be in
    ``(0, 1]`` — a share can never cost more than $1 or be free.
  * A winning YES/NO share pays $1, a losing share pays $0. Profit is net of the stake.
  * ``p`` is your probability for the side you'd buy; ``prob_of_loss`` is just ``1 - p``.
"""

from __future__ import annotations

from decimal import Decimal

ZERO = Decimal(0)
ONE = Decimal(1)


def _check_ask(ask: Decimal) -> None:
    if not (ZERO < ask <= ONE):
        raise ValueError(f"ask must be in (0, 1], got {ask}")


def _check_prob(p: Decimal) -> None:
    if not (ZERO <= p <= ONE):
        raise ValueError(f"probability must be in [0, 1], got {p}")


def shares(stake: Decimal, ask: Decimal) -> Decimal:
    """Number of $1-payout shares ``stake`` dollars buys at the all-in ``ask``: ``stake / ask``."""
    if stake < ZERO:
        raise ValueError(f"stake must be >= 0, got {stake}")
    _check_ask(ask)
    return stake / ask


def profit_if_win(stake: Decimal, ask: Decimal) -> Decimal:
    """Dollar profit if the bet wins: ``shares * $1 - stake = stake * (1 - ask) / ask``.

    Each share you bought for ``ask`` pays out $1, so the profit is the payout minus what
    you staked. ``ask = 1`` (paying full price) yields exactly ``0`` — no edge to win.
    """
    if stake < ZERO:
        raise ValueError(f"stake must be >= 0, got {stake}")
    _check_ask(ask)
    return stake * (ONE - ask) / ask


def profit_if_loss(stake: Decimal) -> Decimal:
    """Dollar profit if the bet loses: ``-stake`` (the share pays $0, you lose what you put in)."""
    if stake < ZERO:
        raise ValueError(f"stake must be >= 0, got {stake}")
    return -stake


def expected_value(stake: Decimal, ask: Decimal, p: Decimal) -> Decimal:
    """Expected dollar profit: ``p * profit_if_win - (1 - p) * stake``.

    Positive EV means the bet pays on average; a single bet can still land on the loss leg.
    """
    _check_prob(p)
    return p * profit_if_win(stake, ask) + (ONE - p) * profit_if_loss(stake)


def prob_of_loss(p: Decimal) -> Decimal:
    """Probability the bet loses: ``1 - p`` (you lose the whole stake on the loss leg)."""
    _check_prob(p)
    return ONE - p


def arb_locked_profit(net_edge: Decimal, set_size: Decimal) -> Decimal:
    """Risk-free arb profit: ``net_edge * set_size`` — locked, independent of the outcome.

    Mirrors the arb branch of :func:`app.math.backtest.realized_pnl`; ``net_edge`` is
    already net of gas + slippage.
    """
    if set_size < ZERO:
        raise ValueError(f"set_size must be >= 0, got {set_size}")
    return net_edge * set_size


def settled_pnl(side: str, stake: Decimal, ask: Decimal, outcome: int) -> Decimal:
    """Realized $ P&L of a resolved directional position. ``side`` is the token you bought
    (``"yes"``/``"no"``); ``outcome`` is the market's YES result (1 = YES, 0 = NO). A YES bet
    wins on outcome 1, a NO bet on outcome 0 — a win pays ``profit_if_win``, a loss ``-stake``.
    Mirrors the directional branch of :func:`app.math.backtest.realized_pnl`."""
    if side not in ("yes", "no"):
        raise ValueError(f"side must be 'yes' or 'no', got {side!r}")
    if outcome not in (0, 1):
        raise ValueError(f"outcome must be 0 or 1, got {outcome}")
    won = (outcome == 1) if side == "yes" else (outcome == 0)
    return profit_if_win(stake, ask) if won else profit_if_loss(stake)


def mark_to_market(share_count: Decimal, current_mid: Decimal, stake: Decimal) -> Decimal:
    """Unrealized P&L of an open position: ``shares * current_mid - stake``.

    Marks the held shares at the current midpoint (their liquidation value) against what
    you paid. Used for live portfolio P&L before a market resolves.
    """
    if share_count < ZERO:
        raise ValueError(f"share_count must be >= 0, got {share_count}")
    if not (ZERO <= current_mid <= ONE):
        raise ValueError(f"current_mid must be in [0, 1], got {current_mid}")
    return share_count * current_mid - stake
