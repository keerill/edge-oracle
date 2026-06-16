import { describe, it, expect } from "vitest";
import type { AdvisedSignal } from "@/lib/schemas/signal";
import { Conflator, mergeSignal } from "@/lib/stream";

function sig(over: Partial<AdvisedSignal>): AdvisedSignal {
  return {
    id: "x",
    time: "2026-06-16T12:00:00+00:00",
    market_id: "m",
    condition_id: "c",
    market_question: "Q",
    strategy: "set_arb",
    kind: "long_set",
    market_price: 0.95,
    p: null,
    edge: 0.05,
    net_edge: 0.03,
    recommended_size_usd: 0,
    recommended_size_pct: 0,
    confidence: 1,
    gate_passed: true,
    gate: null,
    ...over,
  };
}

describe("mergeSignal", () => {
  it("replaces the row with the same id in place", () => {
    const list = [sig({ id: "a", net_edge: 0.03 }), sig({ id: "b", net_edge: 0.04 })];
    const merged = mergeSignal(list, sig({ id: "a", net_edge: 0.07 }));

    expect(merged).toHaveLength(2);
    expect(merged.map((s) => s.id)).toEqual(["a", "b"]); // order preserved
    expect(merged[0]!.net_edge).toBe(0.07); // updated in place
    expect(list[0]!.net_edge).toBe(0.03); // input not mutated
  });

  it("prepends a signal with a new id", () => {
    const list = [sig({ id: "a" })];
    const merged = mergeSignal(list, sig({ id: "z" }));
    expect(merged.map((s) => s.id)).toEqual(["z", "a"]);
  });

  it("starts the list from empty", () => {
    expect(mergeSignal([], sig({ id: "a" })).map((s) => s.id)).toEqual(["a"]);
  });
});

describe("Conflator", () => {
  it("keeps the latest signal per id (last write wins)", () => {
    const c = new Conflator();
    c.push(sig({ id: "a", net_edge: 0.03 }));
    c.push(sig({ id: "b", net_edge: 0.04 }));
    c.push(sig({ id: "a", net_edge: 0.09 })); // overwrites the earlier "a"

    const batch = c.drain();
    expect(batch).toHaveLength(2); // a + b, not 3
    expect(batch.find((s) => s.id === "a")!.net_edge).toBe(0.09);
  });

  it("drains then clears (flush empties the buffer)", () => {
    const c = new Conflator();
    c.push(sig({ id: "a" }));
    expect(c.drain()).toHaveLength(1);
    expect(c.size).toBe(0);
    expect(c.drain()).toEqual([]); // nothing pending after a flush
  });
});
