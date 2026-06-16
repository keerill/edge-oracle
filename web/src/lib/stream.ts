// Pure helpers for the live signal stream — no I/O, unit-testable without a socket.
//
//  * `mergeSignal` — fold a streamed update into the table state (replace the row with the same
//    id, else prepend). SignalsTable sorts client-side, so order here only sets the default.
//  * `Conflator` — the server-side ~10/s cap: hold the latest signal per id, flush-then-clear on
//    a timer so a 100/s inbound burst emits <=10 frames/s (last-write-wins per market).

import type { AdvisedSignal } from "@/lib/schemas/signal";

/** Replace the row with the same `id`, or prepend a new one. Returns a new array. */
export function mergeSignal(list: AdvisedSignal[], incoming: AdvisedSignal): AdvisedSignal[] {
  const idx = list.findIndex((s) => s.id === incoming.id);
  if (idx === -1) return [incoming, ...list];
  const next = list.slice();
  next[idx] = incoming;
  return next;
}

/** Last-write-wins-per-id buffer; `drain` returns the pending batch and clears it. */
export class Conflator {
  private pending = new Map<string, AdvisedSignal>();

  /** Buffer a signal; a later signal with the same id overwrites the earlier one. */
  push(signal: AdvisedSignal): void {
    this.pending.set(signal.id, signal);
  }

  /** Return the buffered signals (insertion order) and clear the buffer. */
  drain(): AdvisedSignal[] {
    if (this.pending.size === 0) return [];
    const batch = [...this.pending.values()];
    this.pending.clear();
    return batch;
  }

  get size(): number {
    return this.pending.size;
  }
}
