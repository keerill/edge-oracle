import { describe, it, expect } from "vitest";
import { buildAreaPath, buildLinePath, linearScale, niceTicks } from "@/lib/charts";

// Pure SVG geometry helpers — no rendering, so they're hand-checkable.

describe("linearScale", () => {
  it("maps domain endpoints onto range endpoints", () => {
    const s = linearScale([0, 10], [0, 100]);
    expect(s(0)).toBe(0);
    expect(s(5)).toBe(50);
    expect(s(10)).toBe(100);
  });

  it("handles an inverted range (SVG y grows downward)", () => {
    const s = linearScale([0, 1], [100, 0]);
    expect(s(0)).toBe(100);
    expect(s(1)).toBe(0);
    expect(s(0.5)).toBe(50);
  });

  it("centers a degenerate (zero-width) domain", () => {
    expect(linearScale([5, 5], [0, 100])(5)).toBe(50);
  });
});

describe("buildLinePath", () => {
  it("returns empty for no points", () => {
    expect(buildLinePath([])).toBe("");
  });

  it("moves to the first point and lines to the rest", () => {
    expect(buildLinePath([{ x: 0, y: 0 }])).toBe("M 0 0");
    expect(buildLinePath([{ x: 0, y: 0 }, { x: 10, y: 5 }])).toBe("M 0 0 L 10 5");
  });

  it("rounds coordinates to two decimals", () => {
    expect(buildLinePath([{ x: 1.5, y: 2.25 }, { x: 3.333, y: 4 }])).toBe("M 1.5 2.25 L 3.33 4");
  });
});

describe("buildAreaPath", () => {
  it("returns empty for no points", () => {
    expect(buildAreaPath([], 100)).toBe("");
  });

  it("closes the line down to the baseline", () => {
    expect(buildAreaPath([{ x: 0, y: 10 }, { x: 10, y: 5 }], 100)).toBe(
      "M 0 10 L 10 5 L 10 100 L 0 100 Z",
    );
  });
});

describe("niceTicks", () => {
  it("produces round ticks spanning a [0,1] domain", () => {
    expect(niceTicks(0, 1, 5)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
  });

  it("produces round ticks spanning a dollar domain", () => {
    expect(niceTicks(0, 1000, 5)).toEqual([0, 200, 400, 600, 800, 1000]);
  });

  it("brackets the data range and stays ascending", () => {
    const ticks = niceTicks(13, 287, 5);
    expect(ticks[0]).toBeLessThanOrEqual(13);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(287);
    for (let i = 1; i < ticks.length; i++) expect(ticks[i]!).toBeGreaterThan(ticks[i - 1]!);
  });

  it("collapses a single-value domain to one tick", () => {
    expect(niceTicks(5, 5)).toEqual([5]);
  });
});
