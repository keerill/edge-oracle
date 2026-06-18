"""Pure arb fill re-check — the gating item before trusting set-arb paper numbers.

A ``set_arb`` signal is detected by walking the book once; by the time a human (or the
paper-capture loop) acts on it, the dislocation may have evaporated. This re-runs the
**existing** set-arb math (``app.math.arb``) on a *fresh* book for the side the signal
fired on, and reports whether the edge survived the latency gap.

Pure: ``Decimal``/``OrderBook`` in, ``ArbFillCheck`` out — no I/O, no clock (both timestamps
are injected). The engine supplies the freshly-fetched books and the check time.

The verdict's ``rechecked_net_edge`` is the *trustworthy* edge: the net edge actually fillable
at action time, which arb paper P&L settles on (never the stale detection-time VWAP).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.math.arb import ArbParams, evaluate_long_set, evaluate_short_set, vwap_to_fill
from app.models.book import OrderBook
from app.models.signal import ArbKind

_MICROS = Decimal(1_000_000)


@dataclass(frozen=True)
class ArbFillCheck:
    """Outcome of re-checking a set-arb's fill on a fresh book.

    ``ok`` — the same-side arb still fully fills at ``set_size`` and clears costs.
    ``rechecked_net_edge`` — the net edge on the fresh book (``None`` when it no longer fires).
    ``latency_s`` — seconds between detection (``advised_at``) and the re-check (``checked_at``).
    ``reason`` — ``"ok"`` | ``"depth_gone"`` | ``"edge_collapsed"`` | ``"flipped_side"`` | ``"no_book"``.
    """

    ok: bool
    rechecked_net_edge: Decimal | None
    latency_s: Decimal
    reason: str


def _latency_s(advised_at: datetime, checked_at: datetime) -> Decimal:
    """Latency in seconds as an exact ``Decimal`` from integer microseconds (no float)."""
    td = checked_at - advised_at
    micros = td.days * 86_400 * 1_000_000 + td.seconds * 1_000_000 + td.microseconds
    return Decimal(micros) / _MICROS


def _diagnose(
    advised_kind: ArbKind,
    yes_book: OrderBook,
    no_book: OrderBook,
    params: ArbParams,
    at: datetime,
) -> str:
    """Label *why* the advised-side arb no longer fires (best-effort diagnostics)."""
    other = evaluate_short_set if advised_kind == "long_set" else evaluate_long_set
    if other(yes_book, no_book, params, market_id="", condition_id="", at=at) is not None:
        return "flipped_side"  # the market dislocated the other way in the window
    if advised_kind == "long_set":
        yes_levels = sorted(yes_book.asks, key=lambda lvl: lvl.price)
        no_levels = sorted(no_book.asks, key=lambda lvl: lvl.price)
    else:
        yes_levels = sorted(yes_book.bids, key=lambda lvl: lvl.price, reverse=True)
        no_levels = sorted(no_book.bids, key=lambda lvl: lvl.price, reverse=True)
    if not yes_levels or not no_levels:
        return "no_book"  # a leg vanished — the set can't be priced
    yes = vwap_to_fill(yes_levels, params.set_size)
    no = vwap_to_fill(no_levels, params.set_size)
    if not (yes.fully_filled and no.fully_filled):
        return "depth_gone"  # the book thinned below the set size
    return "edge_collapsed"  # both legs fill, but the edge no longer clears costs


def check_arb_fill(
    *,
    advised_kind: ArbKind,
    yes_book: OrderBook,
    no_book: OrderBook,
    params: ArbParams,
    advised_at: datetime,
    checked_at: datetime,
) -> ArbFillCheck:
    """Re-check the advised-side set-arb on fresh books. Re-runs the matching evaluator
    (single source of truth for the cost gate); on a miss, diagnoses the reason."""
    latency = _latency_s(advised_at, checked_at)
    evaluate = evaluate_long_set if advised_kind == "long_set" else evaluate_short_set
    sig = evaluate(yes_book, no_book, params, market_id="", condition_id="", at=checked_at)
    if sig is not None:
        return ArbFillCheck(
            ok=True, rechecked_net_edge=sig.net_edge, latency_s=latency, reason="ok"
        )
    reason = _diagnose(advised_kind, yes_book, no_book, params, checked_at)
    return ArbFillCheck(ok=False, rechecked_net_edge=None, latency_s=latency, reason=reason)
