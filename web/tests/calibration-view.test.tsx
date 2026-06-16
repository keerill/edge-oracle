import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import CalibrationView from "@/app/calibration/CalibrationView";
import type { CalibrationSummary } from "@/lib/schemas/report";

const SUMMARY: NonNullable<CalibrationSummary> = {
  overall: { n: 20, brier: 0.1925, log_loss: 0.5612 },
  per_strategy: {
    extreme_correction: { n: 12, brier: 0.18, log_loss: 0.55 },
    set_arb: { n: 8, brier: 0.21, log_loss: 0.58 },
  },
  reliability: [
    { lo: 0, hi: 0.1, count: 0, claimed: null, realized: null },
    { lo: 0.7, hi: 0.8, count: 8, claimed: 0.75, realized: 0.625 },
    { lo: 0.8, hi: 0.9, count: 12, claimed: 0.85, realized: 0.75 },
  ],
  kelly: {
    n_high_conf: 20,
    claimed_avg: 0.81,
    realized_avg: 0.7,
    multiplier: 0.864,
    adjusted_frac: 0.216,
    worst_bin_multiplier: 0.833,
  },
  timeline: [
    { time: "2026-05-01T00:00:00+00:00", n: 10, brier: 0.2, log_loss: 0.6 },
    { time: "2026-06-01T00:00:00+00:00", n: 20, brier: 0.1925, log_loss: 0.5612 },
  ],
};

describe("CalibrationView", () => {
  it("surfaces the suggested Kelly fraction prominently and flags overconfidence", () => {
    render(<CalibrationView summary={SUMMARY} />);
    expect(screen.getByText("Suggested fractional Kelly")).toBeInTheDocument();
    expect(screen.getByText("0.216")).toBeInTheDocument(); // adjusted_frac
    expect(screen.getByText("Overconfident — shrinking")).toBeInTheDocument();
  });

  it("renders both charts and the per-strategy breakdown", () => {
    render(<CalibrationView summary={SUMMARY} />);
    expect(screen.getByRole("heading", { name: "Reliability curve" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Scores over time" })).toBeInTheDocument();
    expect(screen.getByText("Extreme Correction")).toBeInTheDocument();
    expect(screen.getByText("Set Arb")).toBeInTheDocument();
    expect(screen.getAllByRole("img").length).toBeGreaterThanOrEqual(2); // reliability + timeline
  });

  it("shows an empty state for a null (empty-journal) summary", () => {
    render(<CalibrationView summary={null} />);
    expect(screen.getByText(/No resolved markets journaled yet/)).toBeInTheDocument();
  });

  it("holds the base fraction when there are no high-confidence records", () => {
    const noEvidence: NonNullable<CalibrationSummary> = {
      ...SUMMARY,
      kelly: {
        n_high_conf: 0,
        claimed_avg: null,
        realized_avg: null,
        multiplier: null,
        adjusted_frac: null,
        worst_bin_multiplier: null,
      },
    };
    render(<CalibrationView summary={noEvidence} />);
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText(/Not enough high-confidence resolutions/)).toBeInTheDocument();
  });
});
