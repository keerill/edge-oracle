"""Deterministic demo data for the Calibration + Backtest dashboards (dev only — NOT a migration).

Builds a small, hand-shaped universe that exercises both accountability views:

* a calibration journal that is overconfident in its high-confidence bins (so the reliability
  curve bends below the diagonal and the Kelly suggestion shrinks), spread over several days so
  the cumulative Brier/log-loss timeline has movement;
* a handful of extreme-price markets (+ one set-arb) whose stored quotes replay into directional
  longshot bets and an arb, with mixed resolutions so the equity curve has a drawdown and the
  Monte-Carlo distribution has real spread.

Three modes::

    uv run python -m app.seed_demo                # seed the DB + write the resolutions JSON
    uv run python -m app.seed_demo --serve-mock   # no DB: serve /calibration + /backtest from
                                                  #   the real quant math (for local UI preview)
    uv run python -m app.seed_demo --dry-run      # no DB: build everything, print stats

The backtest math runs with demo-friendly knobs (model-error margin 0.02, zero slippage/gas) so
the extreme-correction longshots clear the gate; the same knobs are printed for the live server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.backtest.engine import build_candidates
from app.math.arb import ArbParams
from app.math.backtest import simulate_with_distribution
from app.math.calibration import summarize
from app.models.backtest import BacktestParams, BacktestResult, MarketResolution
from app.models.calibration import CalibrationRecord, CalibrationSummary
from app.models.market import Market
from app.models.quote import QuoteSnapshot

ZERO = Decimal(0)
T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
RESOLUTIONS_PATH = Path(__file__).resolve().parent.parent / "demo_resolutions.json"

# Demo backtest knobs: low margin + zero slippage/gas so the extreme-correction longshots clear
# the gate (the default costs would gate them out, leaving an arb-only — and degenerate — MC).
DEMO_MARGIN = Decimal("0.02")
DEMO_PARAMS = BacktestParams(model_error_margin=DEMO_MARGIN)
DEMO_ARB = ArbParams(set_size=Decimal("1"), gas=ZERO, slippage=ZERO, min_net_edge=Decimal("0.01"))


def _market(n: int, category: str) -> Market:
    return Market(
        market_id=f"demo-m{n}",
        condition_id=f"demo-c{n}",
        question=f"Demo market {n}: will the favourite hold?",
        slug=f"demo-market-{n}",
        category=category,
        event_id=None,
        yes_token_id=f"{n}11",
        no_token_id=f"{n}22",
        enable_order_book=True,
        active=True,
        closed=False,
        liquidity=Decimal("1000"),
    )


def _quote(token: str, market_id: str, *, bid: str, ask: str) -> QuoteSnapshot:
    b, a = Decimal(bid), Decimal(ask)
    return QuoteSnapshot(
        time=T0,
        token_id=token,
        market_id=market_id,
        best_bid=b,
        best_bid_size=Decimal("500"),
        best_ask=a,
        best_ask_size=Decimal("500"),
        midpoint=(a + b) / 2,
        spread=a - b,
    )


# Each entry: (market n, category, YES bid/ask, NO bid/ask, resolved YES?, days-until-resolve).
# Tight spreads keep half-spread small so the corrected fair value clears the gate. Distinct
# categories => distinct correlation-cap tags, so the bets don't clamp each other.
_MARKETS = [
    (1, "politics", ("0.045", "0.055"), ("0.94", "0.95"), 1, 2),   # low YES -> buy YES, wins
    (2, "sports", ("0.055", "0.065"), ("0.93", "0.94"), 0, 3),     # low YES -> buy YES, LOSES
    (3, "crypto", ("0.935", "0.945"), ("0.055", "0.065"), 0, 4),   # high YES -> buy NO, wins
    (4, "economics", ("0.45", "0.46"), ("0.48", "0.49"), 1, 1),    # set-arb, outcome-independent
    (5, "finance", ("0.06", "0.07"), ("0.93", "0.94"), 1, 5),      # low YES -> buy YES, wins
]

# Calibration journal: (strategy, estimate, count, wins). The 0.85 bin is overconfident
# (realized 0.70), which bends the curve and shrinks the Kelly fraction.
_CALIB_GROUPS = [
    ("extreme_correction", "0.85", 10, 7),
    ("favourite_longshot", "0.75", 8, 6),
    ("set_arb", "0.55", 6, 3),
    ("extreme_correction", "0.15", 6, 1),
    ("favourite_longshot", "0.25", 5, 1),
]


def build_markets_quotes() -> tuple[list[Market], list[QuoteSnapshot]]:
    markets: list[Market] = []
    quotes: list[QuoteSnapshot] = []
    for n, cat, (yb, ya), (nb, na), _yes, _days in _MARKETS:
        m = _market(n, cat)
        markets.append(m)
        quotes.append(_quote(m.yes_token_id, m.market_id, bid=yb, ask=ya))
        quotes.append(_quote(m.no_token_id, m.market_id, bid=nb, ask=na))
    return markets, quotes


def build_resolutions() -> dict[str, MarketResolution]:
    return {
        f"demo-c{n}": MarketResolution(outcome=yes, resolve_time=T0 + timedelta(days=days))
        for n, _cat, _yq, _nq, yes, days in _MARKETS
    }


def build_calibration() -> list[CalibrationRecord]:
    records: list[CalibrationRecord] = []
    i = 0
    for strategy, estimate, count, wins in _CALIB_GROUPS:
        for j in range(count):
            day = (i % 6) + 1  # spread across six days so the timeline has movement
            records.append(
                CalibrationRecord(
                    time=datetime(2026, 5, day, 12, 0, tzinfo=timezone.utc),
                    market_id=f"demo-cal-{i}",
                    condition_id=f"demo-cal-c{i}",
                    strategy=strategy,
                    estimate=Decimal(estimate),
                    price=Decimal(estimate),
                    outcome=1 if j < wins else 0,
                )
            )
            i += 1
    records.sort(key=lambda r: r.time)
    return records


def demo_calibration_summary() -> CalibrationSummary:
    return summarize(build_calibration())


def demo_backtest_result() -> BacktestResult:
    markets, quotes = build_markets_quotes()
    resolutions = build_resolutions()
    candidates = build_candidates(
        quotes,
        markets,
        resolutions,
        arb_params=DEMO_ARB,
        model_error_margin=DEMO_MARGIN,
        slippage=ZERO,
        gas=ZERO,
    )
    outcomes = {cid: r.outcome for cid, r in resolutions.items()}
    return simulate_with_distribution(candidates, outcomes, DEMO_PARAMS)


def write_resolutions(path: Path = RESOLUTIONS_PATH) -> Path:
    payload = {
        cid: {"outcome": r.outcome, "resolve_time": r.resolve_time.isoformat()}
        for cid, r in build_resolutions().items()
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


async def _seed_db() -> None:
    from app.db.engine import get_sessionmaker
    from app.ingestion import store

    markets, quotes = build_markets_quotes()
    calibration = build_calibration()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await store.upsert_markets(session, markets)
        await store.insert_quotes(session, quotes)
        await store.insert_calibration(session, calibration)
        await session.commit()
    path = write_resolutions()
    print(f"Seeded {len(markets)} markets, {len(quotes)} quotes, {len(calibration)} calibration rows.")
    print(f"Wrote resolutions -> {path}")
    print("\nRun the API with the demo knobs so the longshots clear the gate:")
    print(
        f"  EDGE_BACKTEST_RESOLUTIONS_PATH={path} EDGE_MODEL_ERROR_MARGIN=0.02 "
        "EDGE_ARB_SLIPPAGE=0 EDGE_ARB_GAS=0 EDGE_ARB_SET_SIZE=1 EDGE_ARB_MIN_NET_EDGE=0.01 \\"
    )
    print("    uv run uvicorn app.main:app --reload")


def _print_stats() -> None:
    summary = demo_calibration_summary()
    result = demo_backtest_result()
    assert summary is not None
    k = summary.kelly
    print("calibration:")
    print(f"  records={summary.overall.n} brier={summary.overall.brier:.4f} log_loss={summary.overall.log_loss:.4f}")
    print(f"  kelly: adjusted_frac={k.adjusted_frac} multiplier={k.multiplier} n_high_conf={k.n_high_conf}")
    print(f"  timeline points={len(summary.timeline)} per_strategy={list(summary.per_strategy)}")
    print("backtest:")
    print(f"  n_bets={result.n_bets} final={result.final_bankroll} return={result.total_return:.4f} max_dd={result.max_drawdown:.4f}")
    mc = result.monte_carlo
    if mc is None:
        print("  monte_carlo=None  (!! no directional bets cleared the gate)")
    else:
        print(f"  monte_carlo: sims={mc.n_sims} p5={mc.final_bankroll_p5} median={mc.final_bankroll_median} p95={mc.final_bankroll_p95} prob_loss={mc.prob_loss:.3f}")


def _serve_mock(port: int) -> None:
    """Serve /calibration + /backtest from the real quant math — no DB. For local UI preview."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    summary = demo_calibration_summary()
    cal_body = (summary.model_dump_json() if summary is not None else "null").encode()
    bt_body = demo_backtest_result().model_dump_json().encode()
    routes = {"/calibration": cal_body, "/backtest": bt_body, "/health": b'{"status":"ok"}'}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib signature)
            body = routes.get(self.path.split("?")[0])
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:  # silence per-request logging
            pass

    print(f"mock quant API on http://localhost:{port}  (/calibration, /backtest)")
    print(f"  point the web app at it:  QUANT_API_URL=http://localhost:{port} pnpm dev")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo calibration + backtest data.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="build + print stats, no DB")
    group.add_argument("--serve-mock", nargs="?", const=8000, type=int, metavar="PORT",
                       help="serve the seeded reports over HTTP (no DB)")
    args = parser.parse_args()

    if args.serve_mock is not None:
        _serve_mock(args.serve_mock)
    elif args.dry_run:
        _print_stats()
        path = write_resolutions()
        print(f"wrote resolutions -> {path}")
    else:
        asyncio.run(_seed_db())


if __name__ == "__main__":
    main()
