import type { EdgeStatus } from "@/components/EdgeMeter";
import type { AdvisedSignal, Strategy } from "@/lib/schemas/signal";

// Presentation helpers for advised signals. Money/probabilities arrive as fractions
// (0.06 = 6 percentage points); these format them for the dashboard. Pure + tested.

/** Net-of-cost edge (a probability/$ fraction) -> basis points, for the EdgeMeter. */
export function edgeToBps(fraction: number): number {
  return Math.round(fraction * 10000);
}

/** Fraction -> percentage string, e.g. 0.05 -> "5.0%". */
export function fmtPct(fraction: number, digits = 1): string {
  return `${(fraction * 100).toFixed(digits)}%`;
}

/** A 0..1 price/probability -> two-decimal string, e.g. 0.4 -> "0.40". */
export function fmtPrice(value: number): string {
  return value.toFixed(2);
}

/** Dollars -> "$50.00". */
export function fmtUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

/** Signed dollars -> "+$18.75" / "-$50.00" (for EV / P&L that can be negative). */
export function fmtUsdSigned(value: number): string {
  const sign = value >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

/** Seconds -> "12.0s" (for fill-check re-read latency). */
export function fmtSeconds(value: number): string {
  return `${value.toFixed(1)}s`;
}

/** The expected-$ headline for a row: directional EV, or the arb's locked (risk-free) profit. */
export function advisedEv(signal: AdvisedSignal): number | null {
  const e = signal.economics;
  if (!e) return null;
  if (e.ev_usd !== null) return e.ev_usd;
  return e.locked_profit_usd;
}

/** Probability the bet loses (directional); 0 for risk-free arb; null when unknown. */
export function advisedLossProb(signal: AdvisedSignal): number | null {
  return signal.economics?.prob_of_loss ?? null;
}

const STRATEGY_LABELS: Record<Strategy, string> = {
  extreme_correction: "Extreme correction",
  favourite_longshot: "Favourite–longshot",
  set_arb: "Set arbitrage",
};

export function strategyLabel(strategy: Strategy): string {
  return STRATEGY_LABELS[strategy];
}

/** The bet side / kind shown as a chip, e.g. "buy_no" -> "BUY NO", "long_set" -> "LONG SET". */
export function sideLabel(kind: string): string {
  return kind.replace(/_/g, " ").toUpperCase();
}

/**
 * The gate/actionability status for the badge — driven by the API's authoritative
 * `gate_passed`, not by re-deriving the money math on the client. A passing gate is "pass";
 * otherwise a negative net edge is "gated" and a non-negative one is "watch".
 */
export function signalStatus(signal: AdvisedSignal): EdgeStatus {
  if (signal.gate_passed) return "pass";
  return signal.net_edge < 0 ? "gated" : "watch";
}
