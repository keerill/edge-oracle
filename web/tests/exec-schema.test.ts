import { describe, it, expect } from "vitest";
import {
  ApprovalResultSchema,
  IntentDetailSchema,
  PendingIntentSchema,
} from "@/lib/schemas/exec";

describe("exec schemas", () => {
  it("parses a pending intent, coercing Decimal-string money to numbers", () => {
    const parsed = PendingIntentSchema.parse({
      intent_id: "i-1",
      source_signal_id: "extreme_correction:m1:1",
      side: "buy_no",
      market_id: "m1",
      condition_id: "c1",
      size: "133.33",
      max_price: "0.30",
      notional_usd: "40",
      created_at: "2026-06-17T12:00:00+00:00",
      expiry: "2026-06-17T12:05:00+00:00",
    });
    expect(parsed.notional_usd).toBe(40);
    expect(parsed.max_price).toBe(0.3);
  });

  it("allows a null max_price", () => {
    const parsed = PendingIntentSchema.parse({
      intent_id: "i-1",
      source_signal_id: "s",
      side: "buy_no",
      market_id: "m",
      condition_id: "c",
      size: "1",
      max_price: null,
      notional_usd: "1",
      created_at: "2026-06-17T12:00:00+00:00",
      expiry: "2026-06-17T12:05:00+00:00",
    });
    expect(parsed.max_price).toBeNull();
  });

  it("parses an intent detail with its audit trail", () => {
    const parsed = IntentDetailSchema.parse({
      intent: {
        intent_id: "i-1",
        source_signal_id: "s",
        side: "buy_no",
        market_id: "m",
        condition_id: "c",
        size: "1",
        max_price: "0.3",
        notional_usd: "1",
        created_at: "2026-06-17T12:00:00+00:00",
        expiry: "2026-06-17T12:05:00+00:00",
      },
      audit: [
        { time: "2026-06-17T12:00:00+00:00", event: "formed", actor: "system", detail: null },
        { time: "2026-06-17T12:00:00+00:00", event: "pending_approval", actor: "system", detail: { mode: "x" } },
      ],
    });
    expect(parsed.audit.map((a) => a.event)).toEqual(["formed", "pending_approval"]);
  });

  it("parses an approval result with a default empty reasons array", () => {
    const parsed = ApprovalResultSchema.parse({ intent_id: "i-1", status: "submitted" });
    expect(parsed.status).toBe("submitted");
    expect(parsed.reasons).toEqual([]);
  });
});
