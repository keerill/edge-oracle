// Typed, server-side client for the quant advisor API. Every response is validated with Zod at
// the boundary (the quant service is treated as untrusted data), so callers get parsed,
// well-typed values — money strings already coerced to numbers. Used by the BFF route handlers
// (app/api/*) and server components; never imported into client code (it reads QUANT_API_URL).

import { QUANT_API_URL } from "@/lib/env";
import {
  AdvisedSignalListSchema,
  AdvisedSignalSchema,
  type AdvisedSignal,
} from "@/lib/schemas/signal";
import {
  BacktestResultSchema,
  CalibrationSummarySchema,
  type BacktestResult,
  type CalibrationSummary,
} from "@/lib/schemas/report";

export class QuantApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "QuantApiError";
  }
}

async function fetchJson(path: string): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(`${QUANT_API_URL}${path}`, {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
  } catch (cause) {
    throw new QuantApiError(`quant service unreachable at ${path}`, 503);
  }
  if (!res.ok) {
    throw new QuantApiError(`quant ${path} returned ${res.status}`, res.status);
  }
  return res.json();
}

export async function getSignals(): Promise<AdvisedSignal[]> {
  return AdvisedSignalListSchema.parse(await fetchJson("/signals"));
}

export async function getSignal(id: string): Promise<AdvisedSignal> {
  // Normalize then encode exactly once — the synthesized id contains ':' (-> %3A), and the
  // incoming route param may arrive already-encoded; decoding first avoids double-encoding it.
  const normalized = encodeURIComponent(decodeURIComponent(id));
  return AdvisedSignalSchema.parse(await fetchJson(`/signals/${normalized}`));
}

export async function getCalibration(): Promise<CalibrationSummary> {
  return CalibrationSummarySchema.parse(await fetchJson("/calibration"));
}

export async function getBacktest(): Promise<BacktestResult> {
  return BacktestResultSchema.parse(await fetchJson("/backtest"));
}
