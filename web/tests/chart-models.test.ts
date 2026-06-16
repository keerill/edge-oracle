import { describe, it, expect } from "vitest";
import type { ChartDims } from "@/lib/charts";
import { reliabilityModel } from "@/components/charts/ReliabilityChart";
import { timelineModel } from "@/components/charts/MetricTimeline";
import { equityModel } from "@/components/charts/EquityCurve";
import { mcModel } from "@/components/charts/MonteCarloChart";

// Pure geometry for the hand-rolled charts: data -> SVG coordinates. Dims are chosen so the
// plot area lands on round numbers (pad 10 -> x,y in [10,90] for a 100x100 box).
const DIMS: ChartDims = { width: 100, height: 100, pad: 10 };

describe("reliabilityModel", () => {
  const bins = [
    { lo: 0, hi: 0.1, count: 1, claimed: 0, realized: 0 },
    { lo: 0.5, hi: 0.6, count: 2, claimed: 0.5, realized: 0.5 },
    { lo: 0.9, hi: 1, count: 0, claimed: null, realized: null },
  ];

  it("drops empty bins and maps claimed->x, realized->y (y inverted)", () => {
    const m = reliabilityModel(bins, DIMS);
    expect(m.points).toHaveLength(2);
    expect({ cx: m.points[0]!.cx, cy: m.points[0]!.cy }).toEqual({ cx: 10, cy: 90 }); // (0,0)
    expect({ cx: m.points[1]!.cx, cy: m.points[1]!.cy }).toEqual({ cx: 50, cy: 50 }); // (.5,.5)
  });

  it("draws the y=x reference across the plot", () => {
    const m = reliabilityModel(bins, DIMS);
    expect(m.diagonal).toEqual({ x1: 10, y1: 90, x2: 90, y2: 10 });
  });

  it("sizes points by sample count (more samples -> bigger dot)", () => {
    const m = reliabilityModel(bins, DIMS);
    expect(m.points[1]!.r).toBeGreaterThan(m.points[0]!.r); // count 2 > count 1
  });

  it("is empty when no bin has samples", () => {
    const m = reliabilityModel([{ lo: 0, hi: 0.1, count: 0, claimed: null, realized: null }], DIMS);
    expect(m.points).toHaveLength(0);
  });
});

describe("timelineModel", () => {
  const points = [
    { time: "2026-01-01T00:00:00+00:00", n: 2, brier: 0.25, log_loss: 0.7 },
    { time: "2026-01-02T00:00:00+00:00", n: 3, brier: 0.2, log_loss: 0.5 },
  ];

  it("spans the plot width and shares a y-domain across both series", () => {
    const m = timelineModel(points, DIMS);
    expect(m.yMax).toBe(0.7); // max over brier+log_loss
    expect(m.brierPoints[0]!.x).toBe(10);
    expect(m.brierPoints[1]!.x).toBe(90);
    // larger loss sits higher on screen (smaller y)
    expect(m.logLossPoints[0]!.y).toBeLessThan(m.brierPoints[0]!.y);
    expect(m.brierPath.startsWith("M 10")).toBe(true);
  });

  it("is empty for an empty journal", () => {
    const m = timelineModel([], DIMS);
    expect(m.brierPath).toBe("");
    expect(m.logLossPath).toBe("");
  });
});

describe("equityModel", () => {
  it("prepends the initial bankroll and finds the peak->trough drawdown", () => {
    const m = equityModel(
      [
        { time: "a", equity: 1150 },
        { time: "b", equity: 1102.5 },
      ],
      1000,
      DIMS,
    );
    expect(m.points).toHaveLength(3); // [initial 1000, 1150, 1102.5]
    expect(m.points[0]).toEqual({ x: 10, y: 90 }); // min equity -> bottom
    expect(m.points[1]).toEqual({ x: 50, y: 10 }); // max equity -> top
    expect(m.drawdown!.peakIndex).toBe(1);
    expect(m.drawdown!.troughIndex).toBe(2);
    expect(m.drawdown!.frac).toBeCloseTo(47.5 / 1150); // 0.041304
  });

  it("reports no drawdown for a monotonically rising curve", () => {
    const m = equityModel([{ time: "a", equity: 1100 }, { time: "b", equity: 1200 }], 1000, DIMS);
    expect(m.drawdown).toBeNull();
  });
});

describe("mcModel", () => {
  const mc = {
    n_sims: 1000,
    final_bankroll_p5: 900,
    final_bankroll_p25: 1000,
    final_bankroll_median: 1100,
    final_bankroll_p75: 1200,
    final_bankroll_p95: 1300,
    final_bankroll_mean: 1090,
    median_max_drawdown: 0.05,
    prob_loss: 0.3,
  };
  const DIMS_W: ChartDims = { width: 200, height: 100, pad: 10 };

  it("maps the bankroll percentiles onto an x-axis bracketing the initial bankroll", () => {
    const m = mcModel(mc, 1000, DIMS_W);
    // domain [min(p5,initial)=900, max(p95,initial)=1300] -> [10,190]
    expect(m.whisker).toEqual({ x1: 10, x2: 190 });
    expect(m.box).toEqual({ x1: 55, x2: 145 }); // p25=1000 -> 55, p75=1200 -> 145
    expect(m.medianX).toBe(100); // p50=1100 -> centre
    expect(m.initialX).toBe(55); // initial 1000 reference
    expect(m.probLoss).toBe(0.3);
  });

  it("keeps the percentile order left-to-right", () => {
    const m = mcModel(mc, 1000, DIMS_W);
    expect(m.whisker.x1).toBeLessThanOrEqual(m.box.x1);
    expect(m.box.x1).toBeLessThanOrEqual(m.medianX);
    expect(m.medianX).toBeLessThanOrEqual(m.box.x2);
    expect(m.box.x2).toBeLessThanOrEqual(m.whisker.x2);
  });
});
