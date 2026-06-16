import { describe, it, expect } from "vitest";
import { AlertSchema } from "@/lib/schemas/alert";

// The quant alert bus sends Decimal value/threshold as JSON strings (same contract as signals);
// the Zod boundary coerces them to numbers and tolerates null (thresholdless alerts).
const RAW = {
  kind: "calibration_drift",
  severity: "warning",
  title: "Calibration drift",
  detail: "high-confidence claimed 0.805 vs realized 0.722 (gap 0.0833 >= threshold 0.05)",
  value: "0.083333",
  threshold: "0.05",
  time: "2026-06-16T12:00:00+00:00",
};

describe("AlertSchema", () => {
  it("coerces Decimal-string value/threshold to numbers", () => {
    const a = AlertSchema.parse(RAW);
    expect(a.value).toBeCloseTo(0.0833);
    expect(a.threshold).toBe(0.05);
    expect(a.kind).toBe("calibration_drift");
    expect(a.severity).toBe("warning");
  });

  it("accepts null value/threshold", () => {
    const a = AlertSchema.parse({ ...RAW, value: null, threshold: null });
    expect(a.value).toBeNull();
    expect(a.threshold).toBeNull();
  });

  it("rejects an unknown kind", () => {
    expect(() => AlertSchema.parse({ ...RAW, kind: "bogus" })).toThrow();
  });

  it("rejects an unknown severity", () => {
    expect(() => AlertSchema.parse({ ...RAW, severity: "critical" })).toThrow();
  });
});
