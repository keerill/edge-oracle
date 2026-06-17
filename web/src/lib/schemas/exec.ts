import { z } from "zod";

// Executor control-API boundary (untrusted, like the quant boundary). Money is Decimal-as-string.
const money = z.coerce.number();

export const PendingIntentSchema = z.object({
  intent_id: z.string(),
  source_signal_id: z.string(),
  side: z.string(),
  market_id: z.string(),
  condition_id: z.string(),
  size: money,
  max_price: money.nullable(),
  notional_usd: money,
  created_at: z.string(),
  expiry: z.string(),
});
export type PendingIntent = z.infer<typeof PendingIntentSchema>;

export const PendingIntentListSchema = z.array(PendingIntentSchema);

export const AuditEntrySchema = z.object({
  time: z.string(),
  event: z.string(),
  actor: z.string().nullable(),
  detail: z.record(z.string(), z.unknown()).nullable(),
});
export type AuditEntry = z.infer<typeof AuditEntrySchema>;

export const IntentDetailSchema = z.object({
  intent: PendingIntentSchema,
  audit: z.array(AuditEntrySchema),
});
export type IntentDetail = z.infer<typeof IntentDetailSchema>;

export const ApprovalResultSchema = z.object({
  intent_id: z.string(),
  status: z.enum(["not_found", "signer_rejected", "submitted"]),
  reasons: z.array(z.string()).default([]),
});
export type ApprovalResult = z.infer<typeof ApprovalResultSchema>;
