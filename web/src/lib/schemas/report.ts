import { z } from "zod";

// Calibration + backtest report schemas — mirror the quant Pydantic models. No page renders
// these yet (signals is the slice's UI), but the typed client returns validated shapes so the
// future calibration/backtest views and the phase-4 streaming layer have a ready contract.
const money = z.coerce.number();

const CalibrationMetricsSchema = z.object({
  n: z.number().int(),
  brier: money,
  log_loss: money,
});

const ReliabilityBinSchema = z.object({
  lo: money,
  hi: money,
  count: z.number().int(),
  claimed: money.nullable(),
  realized: money.nullable(),
});

const KellyAdjustmentSchema = z.object({
  n_high_conf: z.number().int(),
  claimed_avg: money.nullable(),
  realized_avg: money.nullable(),
  multiplier: money.nullable(),
  adjusted_frac: money.nullable(),
  worst_bin_multiplier: money.nullable(),
});

// GET /calibration returns null on an empty journal.
export const CalibrationSummarySchema = z
  .object({
    overall: CalibrationMetricsSchema,
    per_strategy: z.record(z.string(), CalibrationMetricsSchema),
    reliability: z.array(ReliabilityBinSchema),
    kelly: KellyAdjustmentSchema,
  })
  .nullable();
export type CalibrationSummary = z.infer<typeof CalibrationSummarySchema>;

const StrategyBreakdownSchema = z.object({
  strategy: z.string(),
  n: z.number().int(),
  wins: z.number().int(),
  hit_rate: money.nullable(),
  total_pnl: money,
  total_return: money,
  sharpe_like: money.nullable(),
});

export const BacktestResultSchema = z.object({
  initial_bankroll: money,
  final_bankroll: money,
  total_return: money,
  hit_rate: money.nullable(),
  max_drawdown: money,
  sharpe_like: money.nullable(),
  n_bets: z.number().int(),
  per_strategy: z.record(z.string(), StrategyBreakdownSchema),
  equity_curve: z.array(z.object({ time: z.string(), equity: money })),
  closed_bets: z.array(z.unknown()),
});
export type BacktestResult = z.infer<typeof BacktestResultSchema>;
