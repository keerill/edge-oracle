import { describe, it, expect } from "vitest";
import { shouldToastSignal, HIGH_EDGE_THRESHOLD } from "@/lib/notifications";
import type { AdvisedSignal } from "@/lib/schemas/signal";

// shouldToastSignal only reads net_edge; a partial cast keeps the test focused.
const sig = (net_edge: number): AdvisedSignal => ({ net_edge }) as AdvisedSignal;

describe("shouldToastSignal", () => {
  it("does not toast when the new edge is below threshold", () => {
    expect(shouldToastSignal(sig(0.02), sig(0.03))).toBe(false);
  });

  it("toasts on a rising edge across the threshold", () => {
    expect(shouldToastSignal(sig(0.02), sig(0.06))).toBe(true);
  });

  it("does not re-toast when it was already above threshold", () => {
    expect(shouldToastSignal(sig(0.06), sig(0.07))).toBe(false);
  });

  it("toasts a brand-new high signal (no previous)", () => {
    expect(shouldToastSignal(undefined, sig(0.06))).toBe(true);
  });

  it("does not toast a brand-new low signal", () => {
    expect(shouldToastSignal(undefined, sig(0.02))).toBe(false);
  });

  it("treats the threshold as inclusive and defaults to 0.05", () => {
    expect(HIGH_EDGE_THRESHOLD).toBe(0.05);
    expect(shouldToastSignal(undefined, sig(0.05))).toBe(true);
  });
});
