import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import PaperPerformanceView from "@/app/paper-performance/PaperPerformanceView";
import type { PaperPerformance } from "@/lib/schemas/report";

const PERF: PaperPerformance = {
  initial_bankroll: 1000,
  final_bankroll: 1063.8,
  total_pnl: 63.8,
  total_return: 0.0638,
  hit_rate: 0.6,
  max_drawdown: 0.011,
  sharpe_like: 0.42,
  n_closed: 5,
  n_open: 8,
  per_strategy: {
    extreme_correction: {
      strategy: "extreme_correction",
      n: 4,
      wins: 2,
      hit_rate: 0.5,
      total_pnl: 51.3,
      avg_return: 0.12,
      sharpe_like: 0.4,
    },
    set_arb: {
      strategy: "set_arb",
      n: 1,
      wins: 1,
      hit_rate: 1,
      total_pnl: 12.5,
      avg_return: 12.5,
      sharpe_like: null,
    },
  },
  equity_curve: [
    { time: "2026-06-10T02:00:00+00:00", equity: 1010 },
    { time: "2026-06-12T03:00:00+00:00", equity: 1063.8 },
  ],
  arb_fill_assumed: true,
};

describe("PaperPerformanceView", () => {
  it("shows headline KPIs, the equity curve, the per-strategy table and the arb caveat", () => {
    render(<PaperPerformanceView perf={PERF} />);
    expect(screen.getByText("Final bankroll")).toBeInTheDocument();
    expect(screen.getByText("from $1000.00")).toBeInTheDocument();
    expect(screen.getByText("$1063.80")).toBeInTheDocument();
    expect(screen.getByText("6.4%")).toBeInTheDocument(); // total return
    expect(screen.getByText("8")).toBeInTheDocument(); // open trades KPI
    expect(screen.getByRole("heading", { name: "Realized equity" })).toBeInTheDocument();
    expect(screen.getByText("Extreme Correction")).toBeInTheDocument();
    expect(screen.getByText("Set Arb")).toBeInTheDocument();
    // The honesty caveat for set-arb fill-optimism is surfaced.
    expect(screen.getByText(/fill-optimistic/i)).toBeInTheDocument();
    expect(screen.getAllByRole("img").length).toBeGreaterThanOrEqual(1); // equity curve
  });

  it("explains the empty state when no paper trade has settled yet", () => {
    const empty: PaperPerformance = {
      ...PERF,
      final_bankroll: 1000,
      total_pnl: 0,
      total_return: 0,
      hit_rate: null,
      max_drawdown: 0,
      sharpe_like: null,
      n_closed: 0,
      n_open: 8,
      per_strategy: {},
      equity_curve: [],
      arb_fill_assumed: false,
    };
    render(<PaperPerformanceView perf={empty} />);
    expect(screen.getByText(/No paper trades have settled yet/i)).toBeInTheDocument();
    // Still reports how many are open and waiting to resolve.
    expect(screen.getByText(/8 open/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Realized equity" })).toBeNull();
  });
});
