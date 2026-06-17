import { z } from "zod";

// The quant service serializes Decimal money as JSON *strings* (no float in the money path).
// At this boundary we coerce them to numbers for display/sorting — the UI never executes money,
// so number precision is fine here. `money` accepts the incoming string (or a number) -> number.
const money = z.coerce.number();

export const StrategySchema = z.enum([
  "extreme_correction",
  "favourite_longshot",
  "set_arb",
]);
export type Strategy = z.infer<typeof StrategySchema>;

export const GateBreakdownSchema = z.object({
  m: money,
  half_spread: money,
  slippage: money,
  gas: money,
  margin: money,
  p_lo: money,
  threshold: money,
});
export type GateBreakdown = z.infer<typeof GateBreakdownSchema>;

// The dollar view of a bet (computed by quant from the user's bankroll). Most fields are
// nullable so directional + arb share one shape; longshot carries no economics at all.
export const EconomicsSchema = z.object({
  ask: money.nullable(),
  stake_usd: money.nullable(),
  profit_if_win_usd: money.nullable(),
  profit_if_loss_usd: money.nullable(),
  ev_usd: money.nullable(),
  ev_usd_conservative: money.nullable(),
  prob_of_loss: money.nullable(),
  locked_profit_usd: money.nullable(),
});
export type Economics = z.infer<typeof EconomicsSchema>;

export const AdvisedSignalSchema = z.object({
  id: z.string(),
  time: z.string(), // ISO-8601 (UTC)
  market_id: z.string(),
  condition_id: z.string(),
  market_question: z.string().nullable(),
  strategy: StrategySchema,
  kind: z.string(),
  market_price: money,
  p: money.nullable(),
  edge: money,
  net_edge: money,
  recommended_size_usd: money,
  recommended_size_pct: money,
  confidence: money,
  gate_passed: z.boolean(),
  gate: GateBreakdownSchema.nullable(),
  economics: EconomicsSchema.nullable().default(null),
});
export type AdvisedSignal = z.infer<typeof AdvisedSignalSchema>;

export const AdvisedSignalListSchema = z.array(AdvisedSignalSchema);
