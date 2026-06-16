import { z } from "zod";

// The quant alert bus serializes Decimal value/threshold as JSON strings (same no-float-in-money
// contract as signals); coerce to number for display. Both are null for thresholdless alerts.
const money = z.coerce.number().nullable();

export const AlertKindSchema = z.enum(["ws_drop", "drawdown_breach", "calibration_drift"]);
export type AlertKind = z.infer<typeof AlertKindSchema>;

export const AlertSeveritySchema = z.enum(["info", "warning", "error"]);
export type AlertSeverity = z.infer<typeof AlertSeveritySchema>;

export const AlertSchema = z.object({
  kind: AlertKindSchema,
  severity: AlertSeveritySchema,
  title: z.string(),
  detail: z.string(),
  value: money,
  threshold: money,
  time: z.string(), // ISO-8601 (UTC)
});
export type Alert = z.infer<typeof AlertSchema>;
