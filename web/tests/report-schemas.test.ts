import { describe, it, expect } from "vitest";
import {
  BacktestResultSchema,
  CalibrationSummarySchema,
} from "@/lib/schemas/report";

// The quant service sends Decimal money as JSON strings; the boundary must coerce them to
// numbers and surface the cumulative `timeline` (calibration) and `monte_carlo` (backtest)
// shapes the dashboard pages consume.

const RAW_CALIBRATION = {
  overall: { n: 3, brier: "0.166667", log_loss: "0.462098" },
  per_strategy: {
    extreme_correction: { n: 3, brier: "0.166667", log_loss: "0.462098" },
  },
  reliability: [
    { lo: "0.0", hi: "0.1", count: 1, claimed: "0.0", realized: "0.0" },
    { lo: "0.5", hi: "0.6", count: 2, claimed: "0.5", realized: "0.5" },
    { lo: "0.9", hi: "1.0", count: 0, claimed: null, realized: null },
  ],
  kelly: {
    n_high_conf: 10,
    claimed_avg: "0.8",
    realized_avg: "0.6",
    multiplier: "0.75",
    adjusted_frac: "0.1875",
    worst_bin_multiplier: "0.75",
  },
  timeline: [
    { time: "2026-01-01T00:00:00+00:00", n: 2, brier: "0.25", log_loss: "0.693147" },
    { time: "2026-01-02T00:00:00+00:00", n: 3, brier: "0.166667", log_loss: "0.462098" },
  ],
};

const RAW_BACKTEST = {
  initial_bankroll: "1000",
  final_bankroll: "1102.5",
  total_return: "0.1025",
  hit_rate: "0.5",
  max_drawdown: "0.041304",
  sharpe_like: "0.5",
  n_bets: 2,
  per_strategy: {
    extreme_correction: {
      strategy: "extreme_correction",
      n: 2,
      wins: 1,
      hit_rate: "0.5",
      total_pnl: "102.5",
      total_return: "0.1025",
      sharpe_like: "0.5",
    },
  },
  equity_curve: [
    { time: "2026-06-01T02:00:00+00:00", equity: "1150" },
    { time: "2026-06-01T03:00:00+00:00", equity: "1102.5" },
  ],
  closed_bets: [],
  monte_carlo: {
    n_sims: 1000,
    final_bankroll_p5: "950",
    final_bankroll_p25: "1000",
    final_bankroll_median: "1102.5",
    final_bankroll_p75: "1150",
    final_bankroll_p95: "1200",
    final_bankroll_mean: "1080.5",
    median_max_drawdown: "0.03",
    prob_loss: "0.22",
  },
};

describe("CalibrationSummarySchema", () => {
  it("coerces money and exposes the cumulative timeline", () => {
    const parsed = CalibrationSummarySchema.parse(RAW_CALIBRATION);
    expect(parsed).not.toBeNull();
    expect(parsed!.overall.brier).toBeCloseTo(0.166667);
    expect(parsed!.timeline).toHaveLength(2);
    expect(parsed!.timeline[0]!.n).toBe(2);
    expect(parsed!.timeline[0]!.brier).toBe(0.25);
    expect(parsed!.timeline[1]!.brier).toBeCloseTo(0.166667);
    expect(typeof parsed!.timeline[0]!.time).toBe("string");
  });

  it("keeps null kelly diagnostics and empty-bin nulls", () => {
    const parsed = CalibrationSummarySchema.parse({
      ...RAW_CALIBRATION,
      kelly: { ...RAW_CALIBRATION.kelly, n_high_conf: 0, claimed_avg: null, adjusted_frac: null },
    });
    expect(parsed!.kelly.claimed_avg).toBeNull();
    expect(parsed!.kelly.adjusted_frac).toBeNull();
    expect(parsed!.reliability[2]!.claimed).toBeNull();
  });

  it("returns null for an empty journal", () => {
    expect(CalibrationSummarySchema.parse(null)).toBeNull();
  });
});

describe("BacktestResultSchema", () => {
  it("coerces money and exposes the Monte-Carlo distribution", () => {
    const parsed = BacktestResultSchema.parse(RAW_BACKTEST);
    expect(parsed.final_bankroll).toBe(1102.5);
    expect(parsed.equity_curve[0]!.equity).toBe(1150);
    expect(parsed.monte_carlo).not.toBeNull();
    expect(parsed.monte_carlo!.n_sims).toBe(1000);
    expect(parsed.monte_carlo!.final_bankroll_median).toBe(1102.5);
    expect(parsed.monte_carlo!.prob_loss).toBeCloseTo(0.22);
  });

  it("accepts a null distribution (zero-bet replay)", () => {
    const parsed = BacktestResultSchema.parse({ ...RAW_BACKTEST, n_bets: 0, monte_carlo: null });
    expect(parsed.monte_carlo).toBeNull();
  });
});
