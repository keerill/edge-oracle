"""Pure ranking / filtering of advised signals — the "only the safest first" sort.

The default ``/signals`` order is ``net_edge`` descending (the streaming page relies on it).
This module adds an opt-in **safety** order that surfaces the genuinely risk-free bets first
and honestly buckets the rest:

  * tier 0 — ``set_arb``: the only truly risk-free strategy (buy a set < $1, redeem $1).
  * tier 1 — directional bets that **pass the cost gate** (``p_lo`` clears the all-in
    break-even), ranked within the tier by ``net_edge`` (their margin over the gate).
  * tier 2 — everything else (gated-out directional, display-only longshot): never actionable
    money, sorts last.

Pure (no I/O): callers pass in already-enriched ``AdvisedSignal`` objects.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from app.models.advisor import AdvisedSignal


def safety_tier(advised: AdvisedSignal) -> int:
    """Safety bucket: 0 = risk-free arb, 1 = gate-passing directional, 2 = everything else."""
    if advised.strategy == "set_arb":
        return 0
    if advised.gate_passed:
        return 1
    return 2


def safety_rank_key(advised: AdvisedSignal) -> tuple[int, Decimal]:
    """Sort key for the safety order: ``(tier, -net_edge)`` so lower tiers come first and,
    within a tier, the largest net edge (biggest margin over cost) comes first."""
    return (safety_tier(advised), -advised.net_edge)


def rank_signals(
    advised: Iterable[AdvisedSignal],
    *,
    sort: str = "net_edge",
    safe_only: bool = False,
    min_net_edge: Decimal | None = None,
) -> list[AdvisedSignal]:
    """Filter then order a batch of advised signals.

    ``sort`` is ``"net_edge"`` (default, descending) or ``"safety"`` (risk-free first, then
    gate-passing directional by net edge, then the rest). ``safe_only`` keeps only tiers 0–1
    (risk-free arb + gate-passing directional). ``min_net_edge`` drops anything below the
    threshold (applied to every strategy's ``net_edge``).
    """
    items: Sequence[AdvisedSignal] = list(advised)
    if safe_only:
        items = [a for a in items if safety_tier(a) <= 1]
    if min_net_edge is not None:
        items = [a for a in items if a.net_edge >= min_net_edge]

    if sort == "safety":
        return sorted(items, key=safety_rank_key)
    return sorted(items, key=lambda a: a.net_edge, reverse=True)
