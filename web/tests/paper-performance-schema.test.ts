import { describe, it, expect } from "vitest";
import { PaperPerformanceSchema } from "@/lib/schemas/report";

// Quant sends Decimal money as JSON strings; the boundary must coerce to numbers, keep nulls
// (hit_rate/sharpe_like on a thin sample), and preserve the arb_fill_assumed caveat flag.
const RAW = {
  initial_bankroll: "1000",
  final_bankroll: "1063.80",
  total_pnl: "63.80",
  total_return: "0.0638",
  hit_rate: "0.6",
  max_drawdown: "0.011",
  sharpe_like: null,
  n_closed: 5,
  n_open: 8,
  per_strategy: {
    extreme_correction: {
      strategy: "extreme_correction",
      n: 4,
      wins: 2,
      hit_rate: "0.5",
      total_pnl: "51.30",
      avg_return: "0.12",
      sharpe_like: null,
    },
  },
  equity_curve: [{ time: "2026-06-12T03:00:00+00:00", equity: "1063.80" }],
  arb_fill_assumed: true,
};

describe("PaperPerformanceSchema", () => {
  it("coerces Decimal money strings to numbers and preserves nulls + flags", () => {
    const parsed = PaperPerformanceSchema.parse(RAW);
    expect(parsed.final_bankroll).toBe(1063.8);
    expect(parsed.total_return).toBeCloseTo(0.0638);
    expect(parsed.sharpe_like).toBeNull();
    expect(parsed.n_open).toBe(8);
    expect(parsed.per_strategy.extreme_correction!.total_pnl).toBe(51.3);
    expect(parsed.equity_curve[0]!.equity).toBe(1063.8);
    expect(parsed.arb_fill_assumed).toBe(true);
  });
});
