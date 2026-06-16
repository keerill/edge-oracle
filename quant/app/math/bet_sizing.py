"""Pure bet-sizing math — fractional Kelly with a hard cap, the edge gate, and the
per-tag correlation cap.

This is the most correctness-critical module in EdgeOracle, so every function is pure
(``Decimal`` in, ``Decimal``/``bool`` out — no I/O, no clock, no ``Settings``) and is
pinned by hand-computed unit tests that serve as its spec.

Money-math rules baked in (see CLAUDE.md):
  * Kelly fraction for a YES share bought at price ``m`` with your probability ``p`` is
    ``f* = (p - m) / (1 - m)``; no edge (``p <= m``) sizes nothing.
  * Stakes ALWAYS go through fractional Kelly (default quarter) AND a hard per-position
    cap (default 5% of bankroll).
  * The sizing price is the ask you PAY (``mid + half_spread``), never the midpoint; the
    bet is gated on the CI lower bound ``p_lo``, never the mean.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)
ONE = Decimal(1)


def kelly_fraction(p: Decimal, m: Decimal) -> Decimal:
    """Full-Kelly bet fraction ``f* = (p - m) / (1 - m)`` for a YES share priced ``m``
    under your probability ``p``. Returns ``ZERO`` when ``p <= m`` (no edge — take the
    other side). ``p`` must be in ``[0, 1]`` and ``m`` in ``[0, 1)`` (``m = 1`` has no
    defined fraction)."""
    if not (ZERO <= p <= ONE):
        raise ValueError(f"probability p must be in [0, 1], got {p}")
    if not (ZERO <= m < ONE):
        raise ValueError(f"price m must be in [0, 1), got {m}")
    if p <= m:
        return ZERO
    return (p - m) / (ONE - m)


def fractional_kelly(
    p: Decimal,
    m: Decimal,
    frac: Decimal = Decimal("0.25"),
    cap: Decimal = Decimal("0.05"),
) -> Decimal:
    """Fractional Kelly with a hard cap: ``min(frac * kelly_fraction(p, m), cap)``,
    floored at 0 — the fraction of bankroll to stake. ``frac`` (default quarter-Kelly)
    and ``cap`` (default 5% of bankroll) must be non-negative."""
    if frac < ZERO:
        raise ValueError(f"frac must be >= 0, got {frac}")
    if cap < ZERO:
        raise ValueError(f"cap must be >= 0, got {cap}")
    sized = frac * kelly_fraction(p, m)
    return max(ZERO, min(sized, cap))


def edge_gate(
    p_lo: Decimal,
    m: Decimal,
    half_spread: Decimal,
    slippage: Decimal,
    gas: Decimal,
) -> bool:
    """Bet only when the conservative edge clears every cost:
    ``p_lo > m + half_spread + slippage + gas``. ``p_lo`` is the CI **lower** bound
    (never the mean); the comparison is strict, so exact break-even is rejected."""
    return p_lo > m + half_spread + slippage + gas


def position_size(
    bankroll: Decimal,
    p: Decimal,
    p_lo: Decimal,
    m: Decimal,
    half_spread: Decimal,
    slippage: Decimal,
    gas: Decimal,
    frac: Decimal = Decimal("0.25"),
    cap: Decimal = Decimal("0.05"),
) -> Decimal:
    """Dollar stake for one position: gate on ``p_lo``, then size fractional Kelly on
    your probability ``p`` at the ask you pay (``m + half_spread``), times ``bankroll``.

    Returns ``ZERO`` when the edge gate fails. The stake is bounded to
    ``[0, bankroll * cap]`` by the cap inside :func:`fractional_kelly`.
    """
    if bankroll < ZERO:
        raise ValueError(f"bankroll must be >= 0, got {bankroll}")
    if not edge_gate(p_lo, m, half_spread, slippage, gas):
        return ZERO
    ask = m + half_spread
    return bankroll * fractional_kelly(p, ask, frac, cap)


@dataclass(frozen=True)
class TaggedStake:
    """A proposed dollar ``stake`` carrying the macro ``tag`` (theme) it belongs to."""

    tag: str
    stake: Decimal


def cap_correlated_stakes(
    positions: Sequence[TaggedStake], max_per_tag: Decimal
) -> list[TaggedStake]:
    """Cap total exposure per macro theme — one tag, one bet. For each ``tag`` whose
    stakes sum above ``max_per_tag``, scale every position in that group down pro-rata
    (``stake * max_per_tag / group_total``) so the group totals ``max_per_tag`` while
    keeping relative sizing; groups already under the cap are untouched. Input order is
    preserved. All stakes and ``max_per_tag`` must be non-negative.

    The scale is applied as ``stake * max_per_tag / total`` (multiply before divide) so
    exact ratios stay exact — e.g. ``300 * 500 / 600 == 250``, not ``249.999…``.
    """
    if max_per_tag < ZERO:
        raise ValueError(f"max_per_tag must be >= 0, got {max_per_tag}")
    totals: dict[str, Decimal] = {}
    for pos in positions:
        if pos.stake < ZERO:
            raise ValueError(f"stake must be >= 0, got {pos.stake} for tag {pos.tag!r}")
        totals[pos.tag] = totals.get(pos.tag, ZERO) + pos.stake
    capped: list[TaggedStake] = []
    for pos in positions:
        total = totals[pos.tag]
        if total > max_per_tag:
            capped.append(TaggedStake(tag=pos.tag, stake=pos.stake * max_per_tag / total))
        else:
            capped.append(pos)
    return capped
