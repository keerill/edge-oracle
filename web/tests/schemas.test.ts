import { describe, it, expect } from "vitest";
import { AdvisedSignalSchema, AdvisedSignalListSchema } from "@/lib/schemas/signal";

// The quant service sends Decimal money as JSON strings; the Zod boundary must coerce them to
// numbers (and preserve nulls for arb/longshot probability + gate).
const RAW_DIRECTIONAL = {
  id: "extreme_correction:m1:1",
  time: "2026-06-16T12:00:00+00:00",
  market_id: "m1",
  condition_id: "c1",
  market_question: "Will it?",
  strategy: "extreme_correction",
  kind: "buy_yes",
  market_price: "0.40",
  p: "0.55",
  edge: "0.13",
  net_edge: "0.06",
  recommended_size_usd: "50.00",
  recommended_size_pct: "0.05",
  confidence: "0.107143",
  gate_passed: true,
  gate: {
    m: "0.40",
    half_spread: "0.02",
    slippage: "0.01",
    gas: "0.01",
    margin: "0.05",
    p_lo: "0.50",
    threshold: "0.44",
  },
};

describe("AdvisedSignalSchema", () => {
  it("coerces Decimal money strings to numbers", () => {
    const parsed = AdvisedSignalSchema.parse(RAW_DIRECTIONAL);
    expect(parsed.recommended_size_usd).toBe(50);
    expect(parsed.net_edge).toBeCloseTo(0.06);
    expect(parsed.p).toBe(0.55);
    expect(typeof parsed.gate?.threshold).toBe("number");
    expect(parsed.gate?.threshold).toBe(0.44);
  });

  it("keeps null probability and null gate for non-directional signals", () => {
    const arb = AdvisedSignalSchema.parse({
      ...RAW_DIRECTIONAL,
      id: "set_arb:m1:2",
      strategy: "set_arb",
      kind: "long_set",
      p: null,
      gate: null,
      gate_passed: true,
    });
    expect(arb.p).toBeNull();
    expect(arb.gate).toBeNull();
  });

  it("rejects an unknown strategy", () => {
    expect(() => AdvisedSignalSchema.parse({ ...RAW_DIRECTIONAL, strategy: "bogus" })).toThrow();
  });

  it("parses a list", () => {
    const list = AdvisedSignalListSchema.parse([RAW_DIRECTIONAL]);
    expect(list).toHaveLength(1);
    expect(list[0]!.market_question).toBe("Will it?");
  });
});
