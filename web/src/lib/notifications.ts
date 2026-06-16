// Pure helper for high-net-edge opportunity toasts — no I/O, unit-testable.
//
// The signals stream re-fires constantly; we only want a toast when a signal's net edge *crosses
// up* through the threshold (a rising edge), not on every conflated tick while it stays high.

import type { AdvisedSignal } from "@/lib/schemas/signal";

/** Net-of-cost edge (fraction) at/above which a live signal is worth a toast. */
export const HIGH_EDGE_THRESHOLD = 0.05;

/**
 * True only when `next` is at/above the threshold AND it wasn't already (a rising edge):
 * a brand-new high signal (`prev` undefined) toasts; one that was already high does not.
 */
export function shouldToastSignal(
  prev: AdvisedSignal | undefined,
  next: AdvisedSignal,
  threshold: number = HIGH_EDGE_THRESHOLD,
): boolean {
  if (next.net_edge < threshold) return false;
  return prev === undefined || prev.net_edge < threshold;
}
