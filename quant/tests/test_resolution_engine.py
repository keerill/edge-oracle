"""Resolution-watcher orchestration + the calibration->live-Kelly wiring (fakes, no DB/net)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.api.signals import effective_kelly_frac
from app.config import Settings
from app.ingestion import resolution_engine, store
from app.models.calibration import CalibrationRecord
from app.models.market import Market
from app.models.signal import ExtremeCorrectionSignal
from app.polymarket.schemas import RawGammaMarket

AT = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)


def _market(mid, cid) -> Market:
    return Market(
        market_id=mid, condition_id=cid, question="q", slug="s", category=None,
        event_id=None, yes_token_id="y" + mid, no_token_id="n" + mid,
        enable_order_book=True, active=True, closed=False, liquidity=Decimal("1"),
    )


def _signal(mid, cid, fair="0.80", price="0.60") -> ExtremeCorrectionSignal:
    return ExtremeCorrectionSignal(
        time=AT, market_id=mid, condition_id=cid, kind="correction",
        price=Decimal(price), fair_value=Decimal(fair),
    )


def _resolved(cid, prices) -> RawGammaMarket:
    return RawGammaMarket(
        id="g" + cid, question="q", slug="s", conditionId=cid,
        outcomes=["Yes", "No"], outcomePrices=prices, clobTokenIds='["1","2"]',
        enableOrderBook=True, active=True, closed=True,
    )


class FakeGamma:
    def __init__(self, resolved):
        self._resolved = resolved
        self.requested = None

    async def fetch_resolutions(self, condition_ids):
        self.requested = list(condition_ids)
        return list(self._resolved)


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


def fake_sessionmaker():
    return FakeSession()


def _patch(monkeypatch, *, markets, signals, journaled=(), capture=None):
    async def load_markets(session):
        return list(markets)

    async def load_signals(session, *, strategy=None, limit=100):
        return [s for s in signals if strategy is None or getattr(s, "strategy", None) == strategy]

    async def load_calibration(session, strategy=None):
        return list(journaled)

    async def insert_calibration(session, records):
        if capture is not None:
            capture.extend(records)
        return len(list(records))

    monkeypatch.setattr(store, "load_tracked_markets", load_markets)
    monkeypatch.setattr(store, "load_signals", load_signals)
    monkeypatch.setattr(store, "load_calibration", load_calibration)
    monkeypatch.setattr(store, "insert_calibration", insert_calibration)


async def test_journals_resolved_market_with_a_prediction(monkeypatch):
    captured: list[CalibrationRecord] = []
    _patch(
        monkeypatch,
        markets=[_market("m1", "c1")],
        signals=[_signal("m1", "c1", fair="0.80", price="0.60")],
        capture=captured,
    )
    gamma = FakeGamma([_resolved("c1", ["1", "0"])])  # YES won
    result = await resolution_engine.run_resolution_scan_once(
        gamma, fake_sessionmaker, Settings(), now=lambda: AT
    )
    assert result.checked == 1 and result.journaled == 1
    rec = captured[0]
    assert rec.market_id == "m1" and rec.outcome == 1
    assert rec.estimate == Decimal("0.80") and rec.price == Decimal("0.60")


async def test_already_journaled_market_is_skipped(monkeypatch):
    captured: list[CalibrationRecord] = []
    prior = CalibrationRecord(
        time=AT, market_id="m1", condition_id="c1", strategy="extreme_correction",
        estimate=Decimal("0.8"), price=Decimal("0.6"), outcome=1,
    )
    _patch(monkeypatch, markets=[_market("m1", "c1")], signals=[_signal("m1", "c1")],
           journaled=[prior], capture=captured)
    gamma = FakeGamma([_resolved("c1", ["1", "0"])])
    result = await resolution_engine.run_resolution_scan_once(gamma, fake_sessionmaker, Settings())
    assert result.journaled == 0 and captured == []


async def test_resolved_market_without_a_prediction_is_skipped(monkeypatch):
    captured: list[CalibrationRecord] = []
    _patch(monkeypatch, markets=[_market("m1", "c1")], signals=[], capture=captured)
    gamma = FakeGamma([_resolved("c1", ["0", "1"])])
    result = await resolution_engine.run_resolution_scan_once(gamma, fake_sessionmaker, Settings())
    assert result.checked == 1 and result.journaled == 0


# --- position settlement ----------------------------------------------------


def _position(pid, cid, side="yes", entry="0.40", stake="50"):
    from app.models.position import Position

    return Position(
        id=pid, created_at=AT, market_id="m1", condition_id=cid, strategy="extreme_correction",
        side=side, entry_price=Decimal(entry), stake_usd=Decimal(stake),
        shares=Decimal(stake) / Decimal(entry), status="open",
    )


async def test_settles_open_directional_position_on_resolution(monkeypatch):
    settled: list[dict] = []

    async def load_positions(session, *, status=None):
        return [_position("p1", "c1", side="yes")]

    async def settle_position(session, position_id, *, outcome, pnl, resolved_at):
        settled.append({"id": position_id, "outcome": outcome, "pnl": pnl})

    monkeypatch.setattr(store, "load_positions", load_positions)
    monkeypatch.setattr(store, "settle_position", settle_position)
    gamma = FakeGamma([_resolved("c1", ["1", "0"])])  # YES won
    result = await resolution_engine.run_position_settlement_once(
        gamma, fake_sessionmaker, Settings(), now=lambda: AT
    )
    assert result.settled == 1
    # YES bet at 0.40 for $50 won -> profit_if_win = 50*(1-0.40)/0.40 = 75.
    assert settled[0]["outcome"] == 1
    assert settled[0]["pnl"] == Decimal("75")


async def test_settlement_skips_unresolved_and_arb_positions(monkeypatch):
    settled: list[str] = []

    async def load_positions(session, *, status=None):
        return [_position("p1", "c1", side="set"), _position("p2", "c2", side="yes")]

    async def settle_position(session, position_id, *, outcome, pnl, resolved_at):
        settled.append(position_id)

    monkeypatch.setattr(store, "load_positions", load_positions)
    monkeypatch.setattr(store, "settle_position", settle_position)
    # c2 has no resolution row -> nothing to settle; the 'set' position is never directional.
    gamma = FakeGamma([])
    result = await resolution_engine.run_position_settlement_once(
        gamma, fake_sessionmaker, Settings(), now=lambda: AT
    )
    assert result.settled == 0 and settled == []


# --- paper-trade settlement -------------------------------------------------


def _paper(pid, cid, *, side="yes", price="0.40", stake="50", edge="0.06", shares="125"):
    from app.models.paper_trade import PaperTrade

    return PaperTrade(
        id=pid, advised_at=AT, strategy="extreme_correction" if side != "set" else "set_arb",
        market_id="m1", condition_id=cid, side=side, advised_price=Decimal(price),
        stake_usd=Decimal(stake), shares=Decimal(shares), edge=Decimal(edge),
    )


def _patch_paper(monkeypatch, *, open_trades, settled):
    async def load_paper_trades(session, *, status=None):
        return list(open_trades)

    async def settle_paper_trade(session, paper_trade_id, *, outcome, realized_pnl, resolved_at,
                                 status="closed"):
        settled.append({"id": paper_trade_id, "outcome": outcome, "pnl": realized_pnl})

    monkeypatch.setattr(store, "load_paper_trades", load_paper_trades)
    monkeypatch.setattr(store, "settle_paper_trade", settle_paper_trade)


async def test_paper_directional_settles_against_real_outcome(monkeypatch):
    settled: list[dict] = []
    _patch_paper(monkeypatch, open_trades=[_paper("pt1", "c1", side="yes")], settled=settled)
    gamma = FakeGamma([_resolved("c1", ["1", "0"])])  # YES won
    result = await resolution_engine.run_paper_settlement_once(
        gamma, fake_sessionmaker, Settings(), now=lambda: AT
    )
    assert result.directional_settled == 1 and result.arb_settled == 0
    # YES paper bet at all-in 0.40 for $50 won -> 50*(1-0.40)/0.40 = 75.
    assert settled[0]["outcome"] == 1 and settled[0]["pnl"] == Decimal("75")


async def test_paper_arb_settles_immediately_at_locked_edge(monkeypatch):
    settled: list[dict] = []
    arb = _paper("pt2", "c2", side="set", price="0.96", stake="1", edge="0.03", shares="1")
    _patch_paper(monkeypatch, open_trades=[arb], settled=settled)
    gamma = FakeGamma([])  # no resolution needed for arb
    result = await resolution_engine.run_paper_settlement_once(
        gamma, fake_sessionmaker, Settings(), now=lambda: AT
    )
    assert result.arb_settled == 1 and result.directional_settled == 0
    # locked profit = edge * shares = 0.03 * 1; outcome-independent (None).
    assert settled[0]["outcome"] is None and settled[0]["pnl"] == Decimal("0.03")


async def test_paper_arb_settles_on_rechecked_edge(monkeypatch):
    settled: list[dict] = []
    # detection-time edge 0.03, but the fill re-check found only 0.02 fillable -> settle on 0.02
    arb = _paper("pt4", "c4", side="set", price="0.96", stake="1", edge="0.03", shares="1")
    arb = arb.model_copy(update={"fill_ok": True, "rechecked_net_edge": Decimal("0.02")})
    _patch_paper(monkeypatch, open_trades=[arb], settled=settled)
    gamma = FakeGamma([])
    result = await resolution_engine.run_paper_settlement_once(
        gamma, fake_sessionmaker, Settings(), now=lambda: AT
    )
    assert result.arb_settled == 1
    assert settled[0]["pnl"] == Decimal("0.02")  # rechecked edge, not the stale 0.03


async def test_paper_directional_unresolved_is_left_open(monkeypatch):
    settled: list[dict] = []
    _patch_paper(monkeypatch, open_trades=[_paper("pt3", "c3", side="yes")], settled=settled)
    gamma = FakeGamma([])  # c3 not resolved
    result = await resolution_engine.run_paper_settlement_once(
        gamma, fake_sessionmaker, Settings(), now=lambda: AT
    )
    assert result.directional_settled == 0 and settled == []


# --- the calibration -> live Kelly wiring -----------------------------------

async def test_effective_frac_falls_back_on_empty_journal(monkeypatch):
    async def empty(session, strategy=None):
        return []

    monkeypatch.setattr(store, "load_calibration", empty)
    s = Settings(kelly_frac=Decimal("0.25"))
    assert await effective_kelly_frac(None, s.kelly_frac) == Decimal("0.25")


async def test_effective_frac_shrinks_when_overconfident(monkeypatch):
    # ten high-confidence p=0.8 predictions, only 6 win -> overconfident -> shrink below 0.25
    recs = [
        CalibrationRecord(time=AT + timedelta(seconds=i), market_id=f"m{i}", condition_id=f"c{i}",
                          strategy="extreme_correction", estimate=Decimal("0.8"),
                          price=Decimal("0.6"), outcome=(1 if i < 6 else 0))
        for i in range(10)
    ]

    async def load(session, strategy=None):
        return recs

    monkeypatch.setattr(store, "load_calibration", load)
    s = Settings(kelly_frac=Decimal("0.25"))
    frac = await effective_kelly_frac(None, s.kelly_frac)
    assert frac < Decimal("0.25")  # shrunk by the overconfidence
    assert frac == Decimal("0.1875")  # 0.25 * (6/8 realized/claimed in the high-conf bins)
