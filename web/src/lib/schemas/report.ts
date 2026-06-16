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

// One point of the cumulative Brier/log-loss-over-time curve (the final point == overall).
const CalibrationTimePointSchema = z.object({
  time: z.string(),
  n: z.number().int(),
  brier: money,
  log_loss: money,
});

// GET /calibration returns null on an empty journal.
export const CalibrationSummarySchema = z
  .object({
    overall: CalibrationMetricsSchema,
    per_strategy: z.record(z.string(), CalibrationMetricsSchema),
    reliability: z.array(ReliabilityBinSchema),
    kelly: KellyAdjustmentSchema,
    timeline: z.array(CalibrationTimePointSchema),
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

// Resampled-outcome distribution (variance, not just the median). Null on a zero-bet replay.
const MonteCarloSchema = z.object({
  n_sims: z.number().int(),
  final_bankroll_p5: money,
  final_bankroll_p25: money,
  final_bankroll_median: money,
  final_bankroll_p75: money,
  final_bankroll_p95: money,
  final_bankroll_mean: money,
  median_max_drawdown: money,
  prob_loss: money,
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
  monte_carlo: MonteCarloSchema.nullable(),
});
export type BacktestResult = z.infer<typeof BacktestResultSchema>;
