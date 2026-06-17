"""Pure enrichment: a detected ``Signal`` + the current quote -> an ``AdvisedSignal``.

No I/O, no clock, no ``Settings`` — knobs are explicit ``Decimal`` args and the math reuses
the already-tested primitives in ``app.math.bet_sizing`` (``position_size``/``edge_gate``).
This is the load-bearing guarantee: because the directional path mirrors the backtest's
``_directional_candidate`` mapping exactly (``fair_value`` -> ``p_yes`` -> side -> ``p_side``,
``p_lo = p_side - margin``, ``m``/``half_spread`` from the **side token's** own quote), the live
advisor and the replay agree on the recommended stake.

Per-strategy semantics:
  * ``extreme_correction`` (directional): full fractional-Kelly sizing + cost gate. ``edge`` is
    over the ask you pay; ``net_edge = p_lo - threshold`` (the conservative net the gate tests);
    ``confidence`` measures how far ``p_lo`` clears break-even, normalized to ``[0, 1]``.
  * ``set_arb``: risk-free locked edge — no Kelly ``p``, ``confidence = 1``, no cost gate.
  * ``favourite_longshot``: display-only heuristic — no probability, no Kelly size,
    ``confidence = edge_score`` (already a normalized [0, 1] bias strength).

``confidence`` is a *signal-local* heuristic for now; the calibration journal
(``app.math.calibration``) will later shrink it / the Kelly fraction when the models prove
overconfident.
"""

from __future__ import annotations

from decimal import Decimal

from app.math.bet_sizing import edge_gate, position_size
from app.math.profit import (
    arb_locked_profit,
    expected_value,
    prob_of_loss,
    profit_if_loss,
    profit_if_win,
)
from app.models.advisor import AdvisedSignal, Economics, GateBreakdown
from app.models.quote import QuoteSnapshot
from app.models.signal import (
    ArbSignal,
    ExtremeCorrectionSignal,
    FavouriteLongshotSignal,
    Signal,
)

ZERO = Decimal(0)
ONE = Decimal(1)


def _confidence(p_lo: Decimal, threshold: Decimal) -> Decimal:
    """How far the conservative estimate clears the all-in break-even, normalized to ``[0, 1]``:
    ``clamp((p_lo - threshold) / (1 - threshold), 0, 1)``. 0 at (or below) break-even, ->1 as
    ``p_lo`` -> certainty. Degenerate ``threshold >= 1`` (no room above cost) -> 0."""
    if threshold >= ONE:
        return ZERO
    raw = (p_lo - threshold) / (ONE - threshold)
    return max(ZERO, min(ONE, raw))


def _advise_directional(
    signal: ExtremeCorrectionSignal,
    *,
    signal_id: str,
    yes_quote: QuoteSnapshot | None,
    no_quote: QuoteSnapshot | None,
    bankroll: Decimal,
    frac: Decimal,
    cap: Decimal,
    slippage: Decimal,
    gas: Decimal,
    model_error_margin: Decimal,
) -> AdvisedSignal:
    p_yes = signal.fair_value
    side = "yes" if signal.fair_value > signal.price else "no"
    side_quote = yes_quote if side == "yes" else no_quote
    p_side = p_yes if side == "yes" else ONE - p_yes
    kind = "buy_yes" if side == "yes" else "buy_no"

    # Degrade (mirrors _directional_candidate returning None): without a usable side quote we
    # can't price the ask, so there is no sizable bet — surface it as gated, zero-size.
    if side_quote is None or side_quote.midpoint is None or side_quote.spread is None:
        return AdvisedSignal(
            id=signal_id,
            time=signal.time,
            market_id=signal.market_id,
            condition_id=signal.condition_id,
            strategy="extreme_correction",
            kind=kind,
            market_price=signal.price,
            p=p_side,
            edge=ZERO,
            net_edge=ZERO,
            recommended_size_usd=ZERO,
            recommended_size_pct=ZERO,
            confidence=ZERO,
            gate_passed=False,
            gate=None,
        )

    m = side_quote.midpoint
    half_spread = side_quote.spread / Decimal(2)
    p_lo = p_side - model_error_margin
    threshold = m + half_spread + slippage + gas
    ask = m + half_spread

    gate_passed = edge_gate(p_lo, m, half_spread, slippage, gas)
    size = position_size(bankroll, p_side, p_lo, m, half_spread, slippage, gas, frac, cap)
    pct = size / bankroll if bankroll > ZERO else ZERO

    return AdvisedSignal(
        id=signal_id,
        time=signal.time,
        market_id=signal.market_id,
        condition_id=signal.condition_id,
        strategy="extreme_correction",
        kind=kind,
        market_price=m,
        p=p_side,
        edge=p_side - ask,  # edge over the price you actually pay (can be negative)
        net_edge=p_lo - threshold,  # the conservative net the gate tests (the sort key)
        recommended_size_usd=size,
        recommended_size_pct=pct,
        confidence=_confidence(p_lo, threshold),
        gate_passed=gate_passed,
        gate=GateBreakdown(
            m=m,
            half_spread=half_spread,
            slippage=slippage,
            gas=gas,
            margin=model_error_margin,
            p_lo=p_lo,
            threshold=threshold,
        ),
        economics=_directional_economics(size, threshold, p_side, p_lo),
    )


def _directional_economics(
    stake: Decimal, threshold: Decimal, p_side: Decimal, p_lo: Decimal
) -> Economics | None:
    """The dollar view of a directional bet. Cost basis is the all-in ``threshold`` (so EV
    matches the backtest's realized P&L); ``ev_usd`` uses your mean ``p_side`` and
    ``ev_usd_conservative`` the gated CI lower bound ``p_lo`` (clamped to ``[0, 1]``).
    Returns ``None`` only when ``threshold`` is outside ``(0, 1]`` (no payable share)."""
    if not (ZERO < threshold <= ONE):
        return None
    p_lo_clamped = max(ZERO, min(ONE, p_lo))
    return Economics(
        ask=threshold,
        stake_usd=stake,
        profit_if_win_usd=profit_if_win(stake, threshold),
        profit_if_loss_usd=profit_if_loss(stake),
        ev_usd=expected_value(stake, threshold, p_side),
        ev_usd_conservative=expected_value(stake, threshold, p_lo_clamped),
        prob_of_loss=prob_of_loss(p_side),
    )


def _advise_arb(signal: ArbSignal, *, signal_id: str) -> AdvisedSignal:
    # Risk-free: the locked edge is outcome-independent, so confidence is maximal. Advisor-only
    # this slice — we surface the edge but do not size the live set (no execution).
    return AdvisedSignal(
        id=signal_id,
        time=signal.time,
        market_id=signal.market_id,
        condition_id=signal.condition_id,
        strategy="set_arb",
        kind=signal.kind,
        market_price=signal.yes_price + signal.no_price,  # the set cost
        p=None,
        edge=signal.gross_edge,
        net_edge=signal.net_edge,
        recommended_size_usd=ZERO,
        recommended_size_pct=ZERO,
        confidence=ONE,
        gate_passed=True,
        gate=None,
        economics=Economics(
            # Risk-free: the locked edge is the profit, independent of the outcome.
            locked_profit_usd=arb_locked_profit(signal.net_edge, signal.set_size),
            prob_of_loss=ZERO,
        ),
    )


def _advise_longshot(signal: FavouriteLongshotSignal, *, signal_id: str) -> AdvisedSignal:
    # Display-only heuristic: no probability, so no Kelly bet and no money edge this slice.
    # ``edge_score`` is a dimensionless [0, 1] bias strength (NOT a probability/$ edge), so it
    # feeds ``confidence`` only — keeping ``edge``/``net_edge`` at 0 so it never out-ranks an
    # actionable money edge in the net-edge sort.
    return AdvisedSignal(
        id=signal_id,
        time=signal.time,
        market_id=signal.market_id,
        condition_id=signal.condition_id,
        strategy="favourite_longshot",
        kind=signal.kind,
        market_price=signal.price,
        p=None,
        edge=ZERO,
        net_edge=ZERO,
        recommended_size_usd=ZERO,
        recommended_size_pct=ZERO,
        confidence=signal.edge_score,
        gate_passed=False,
        gate=None,
    )


def advise(
    signal: Signal,
    *,
    signal_id: str,
    market_question: str | None = None,
    yes_quote: QuoteSnapshot | None = None,
    no_quote: QuoteSnapshot | None = None,
    bankroll: Decimal,
    frac: Decimal = Decimal("0.25"),
    cap: Decimal = Decimal("0.05"),
    slippage: Decimal = Decimal("0.01"),
    gas: Decimal = Decimal("0.01"),
    model_error_margin: Decimal = Decimal("0.05"),
) -> AdvisedSignal:
    """Enrich a detected ``signal`` with live sizing. ``yes_quote``/``no_quote`` are the
    market's two token snapshots (directional sizing picks the *side* token's own quote); they
    are ignored by arb (priced from the signal's own VWAPs) and longshot (display-only).
    ``market_question`` is the human-readable title for the table (the signals row has only ids).
    """
    if isinstance(signal, ExtremeCorrectionSignal):
        base = _advise_directional(
            signal,
            signal_id=signal_id,
            yes_quote=yes_quote,
            no_quote=no_quote,
            bankroll=bankroll,
            frac=frac,
            cap=cap,
            slippage=slippage,
            gas=gas,
            model_error_margin=model_error_margin,
        )
    elif isinstance(signal, ArbSignal):
        base = _advise_arb(signal, signal_id=signal_id)
    else:
        base = _advise_longshot(signal, signal_id=signal_id)
    return base.model_copy(update={"market_question": market_question})
