import { describe, it, expect } from "vitest";
import { edgeMeterModel } from "@/components/EdgeMeter";

describe("edgeMeterModel", () => {
  it("marks edge clearing the gate as pass", () => {
    const m = edgeMeterModel(184, 70);
    expect(m.status).toBe("pass");
  });

  it("marks positive edge below the gate as watch", () => {
    const m = edgeMeterModel(42, 65);
    expect(m.status).toBe("watch");
  });

  it("marks negative edge as gated", () => {
    const m = edgeMeterModel(-18, 55);
    expect(m.status).toBe("gated");
  });

  it("treats edge exactly equal to the gate as pass (inclusive)", () => {
    expect(edgeMeterModel(70, 70).status).toBe("pass");
  });

  it("clamps negative fill to 0 and never exceeds 100", () => {
    expect(edgeMeterModel(-50, 60).fillPct).toBe(0);
    const capped = edgeMeterModel(10_000, 60, 100);
    expect(capped.fillPct).toBe(100);
  });

  it("places the threshold tick proportionally within the domain", () => {
    // domainMax forced to 200 → gate 100 sits at the midpoint.
    const m = edgeMeterModel(150, 100, 200);
    expect(m.thresholdPct).toBeCloseTo(50, 5);
    expect(m.fillPct).toBeCloseTo(75, 5);
  });

  it("keeps a positive domain even with zero/negative inputs", () => {
    expect(edgeMeterModel(0, 0).domainMax).toBeGreaterThanOrEqual(1);
  });
});
