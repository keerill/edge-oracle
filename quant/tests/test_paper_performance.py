"""Offline worked-example tests for the pure paper-performance math (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.math.paper_performance import summarize_paper_trades
from app.models.paper_trade import PaperTrade

T0 = datetime(2026, 6, 1, tzinfo=UTC)


def _closed(
    pid,
    *,
    strategy="extreme_correction",
    side="yes",
    stake="50",
    pnl="75",
    outcome=1,
    resolved_offset_h=1,
) -> PaperTrade:
    return PaperTrade(
        id=pid,
        advised_at=T0,
        strategy=strategy,
        market_id="m" + pid,
        condition_id="c" + pid,
        side=side,
        advised_price=Decimal("0.40"),
        stake_usd=Decimal(stake),
        shares=Decimal(stake) / Decimal("0.40"),
        edge=Decimal("0.06"),
        status="closed",
        outcome=outcome,
        realized_pnl=Decimal(pnl),
        resolved_at=T0 + timedelta(hours=resolved_offset_h),
    )


def test_empty_is_zero_bet_report() -> None:
    perf = summarize_paper_trades([], initial_bankroll=Decimal("1000"))
    assert perf.n_closed == 0 and perf.final_bankroll == Decimal("1000")
    assert perf.total_return == Decimal("0")
    assert perf.hit_rate is None and perf.sharpe_like is None
    assert perf.max_drawdown == Decimal("0")
    assert perf.equity_curve == ()
    assert perf.arb_fill_assumed is False


def test_two_directional_bets_worked_example() -> None:
    win = _closed("1", pnl="75", outcome=1, resolved_offset_h=1)  # return +1.5
    loss = _closed("2", pnl="-50", outcome=0, resolved_offset_h=2)  # return -1.0
    perf = summarize_paper_trades([win, loss], initial_bankroll=Decimal("1000"), n_open=3)

    assert perf.n_closed == 2 and perf.n_open == 3
    assert perf.total_pnl == Decimal("25")
    assert perf.final_bankroll == Decimal("1025")
    assert perf.total_return == Decimal("0.025")  # 25 / 1000
    assert perf.hit_rate == Decimal("0.5")
    # equity [1000, 1075, 1025] -> peak 1075, trough 1025 -> dd = 50/1075
    assert perf.max_drawdown == Decimal("50") / Decimal("1075")
    # returns [1.5, -1.0]: mean 0.25, var 1.5625, std 1.25 -> 0.25/1.25 = 0.2
    assert perf.sharpe_like == Decimal("0.2")
    assert len(perf.equity_curve) == 2
    assert perf.equity_curve[0].equity == Decimal("1075")
    assert perf.equity_curve[1].equity == Decimal("1025")


def test_per_strategy_breakdown_and_arb_flag() -> None:
    direc = _closed("1", strategy="extreme_correction", pnl="75")
    arb = _closed("2", strategy="set_arb", side="set", stake="1", pnl="0.03", outcome=None)
    perf = summarize_paper_trades([direc, arb], initial_bankroll=Decimal("1000"))

    assert set(perf.per_strategy) == {"extreme_correction", "set_arb"}
    ec = perf.per_strategy["extreme_correction"]
    assert ec.n == 1 and ec.wins == 1 and ec.total_pnl == Decimal("75")
    arbp = perf.per_strategy["set_arb"]
    assert arbp.n == 1 and arbp.total_pnl == Decimal("0.03")
    assert perf.arb_fill_assumed is True  # legacy arb (no fill check) -> still optimistic


def test_arb_fill_assumed_false_when_verified() -> None:
    arb = _closed("2", strategy="set_arb", side="set", stake="1", pnl="0.03", outcome=None)
    verified = arb.model_copy(update={"fill_ok": True, "rechecked_net_edge": Decimal("0.03")})
    perf = summarize_paper_trades([verified], initial_bankroll=Decimal("1000"))
    assert perf.arb_fill_assumed is False  # fill-verified at capture -> caveat drops


def test_open_trades_excluded_from_scoring() -> None:
    closed = _closed("1", pnl="75")
    still_open = PaperTrade(
        id="2", advised_at=T0, strategy="extreme_correction", market_id="m2",
        condition_id="c2", side="yes", advised_price=Decimal("0.40"),
        stake_usd=Decimal("50"), shares=Decimal("125"), edge=Decimal("0.06"),
    )
    perf = summarize_paper_trades([closed, still_open], initial_bankroll=Decimal("1000"))
    assert perf.n_closed == 1  # the open one is ignored by the scorer
