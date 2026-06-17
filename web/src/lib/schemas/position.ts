import { z } from "zod";
import { StrategySchema } from "@/lib/schemas/signal";

const money = z.coerce.number();

export const PositionSideSchema = z.enum(["yes", "no", "set"]);
export type PositionSide = z.infer<typeof PositionSideSchema>;

export const PositionSchema = z.object({
  id: z.string(),
  created_at: z.string(),
  market_id: z.string(),
  condition_id: z.string(),
  strategy: z.string(),
  side: PositionSideSchema,
  entry_price: money,
  stake_usd: money,
  shares: money,
  status: z.enum(["open", "closed"]),
  outcome: z.number().nullable().default(null),
  pnl: money.nullable().default(null),
  resolved_at: z.string().nullable().default(null),
  signal_id: z.string().nullable().default(null),
});
export type Position = z.infer<typeof PositionSchema>;

export const PositionWithPnlSchema = z.object({
  position: PositionSchema,
  current_mid: money.nullable().default(null),
  unrealized_pnl: money.nullable().default(null),
});
export type PositionWithPnl = z.infer<typeof PositionWithPnlSchema>;

export const PositionsResponseSchema = z.object({
  positions: z.array(PositionWithPnlSchema),
  total_exposure: money,
  total_unrealized_pnl: money,
  total_realized_pnl: money,
});
export type PositionsResponse = z.infer<typeof PositionsResponseSchema>;

// POST body to record a placed bet (the server derives id/created_at/shares).
export const OpenPositionRequestSchema = z.object({
  market_id: z.string(),
  condition_id: z.string(),
  strategy: StrategySchema,
  side: PositionSideSchema,
  entry_price: z.number(),
  stake_usd: z.number(),
  signal_id: z.string().nullable().optional(),
});
export type OpenPositionRequest = z.infer<typeof OpenPositionRequestSchema>;
