import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import BacktestView from "@/app/backtest/BacktestView";
import type { BacktestResult } from "@/lib/schemas/report";

const RESULT: BacktestResult = {
  initial_bankroll: 1000,
  final_bankroll: 1102.5,
  total_return: 0.1025,
  hit_rate: 0.5,
  max_drawdown: 0.041304,
  sharpe_like: 0.5,
  n_bets: 2,
  per_strategy: {
    extreme_correction: {
      strategy: "extreme_correction",
      n: 2,
      wins: 1,
      hit_rate: 0.5,
      total_pnl: 102.5,
      total_return: 0.1025,
      sharpe_like: 0.5,
    },
  },
  equity_curve: [
    { time: "2026-06-01T02:00:00+00:00", equity: 1150 },
    { time: "2026-06-01T03:00:00+00:00", equity: 1102.5 },
  ],
  closed_bets: [],
  monte_carlo: {
    n_sims: 1000,
    final_bankroll_p5: 950,
    final_bankroll_p25: 1000,
    final_bankroll_median: 1102.5,
    final_bankroll_p75: 1150,
    final_bankroll_p95: 1200,
    final_bankroll_mean: 1080.5,
    median_max_drawdown: 0.03,
    prob_loss: 0.22,
  },
};

describe("BacktestView", () => {
  it("shows headline KPIs, the equity curve and the Monte-Carlo distribution", () => {
    render(<BacktestView result={RESULT} />);
    expect(screen.getByText("Final bankroll")).toBeInTheDocument();
    expect(screen.getByText("from $1000.00")).toBeInTheDocument(); // initial bankroll hint
    expect(screen.getAllByText("$1102.50").length).toBeGreaterThanOrEqual(1); // final + MC median
    expect(screen.getByRole("heading", { name: "Equity curve" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Monte-Carlo distribution" })).toBeInTheDocument();
    expect(screen.getByText("22.0%")).toBeInTheDocument(); // prob of loss in the caption
    expect(screen.getByText("Extreme Correction")).toBeInTheDocument();
    expect(screen.getAllByRole("img").length).toBeGreaterThanOrEqual(2); // equity + monte-carlo
  });

  it("explains the empty state when the replay took no bets", () => {
    const zero: BacktestResult = {
      ...RESULT,
      n_bets: 0,
      final_bankroll: 1000,
      total_return: 0,
      hit_rate: null,
      max_drawdown: 0,
      sharpe_like: null,
      per_strategy: {},
      equity_curve: [],
      closed_bets: [],
      monte_carlo: null,
    };
    render(<BacktestView result={zero} />);
    expect(screen.getByText(/No resolved bets in this replay/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Equity curve" })).toBeNull();
  });
});
