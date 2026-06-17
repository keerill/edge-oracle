// Typed, server-side client for the quant advisor API. Every response is validated with Zod at
// the boundary (the quant service is treated as untrusted data), so callers get parsed,
// well-typed values — money strings already coerced to numbers. Used by the BFF route handlers
// (app/api/*) and server components; never imported into client code (it reads QUANT_API_URL).

import { QUANT_API_URL, QUANT_API_KEY } from "@/lib/env";
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
import { UserConfigSchema, type UserConfig } from "@/lib/schemas/config";
import {
  PositionSchema,
  PositionsResponseSchema,
  type OpenPositionRequest,
  type Position,
  type PositionsResponse,
} from "@/lib/schemas/position";

export class QuantApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "QuantApiError";
  }
}

function headers(): Record<string, string> {
  return {
    accept: "application/json",
    // Shared secret between the BFF and quant (sent only when configured).
    ...(QUANT_API_KEY ? { "X-API-Key": QUANT_API_KEY } : {}),
  };
}

async function fetchJson(path: string): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(`${QUANT_API_URL}${path}`, {
      cache: "no-store",
      headers: headers(),
    });
  } catch (cause) {
    throw new QuantApiError(`quant service unreachable at ${path}`, 503);
  }
  if (!res.ok) {
    throw new QuantApiError(`quant ${path} returned ${res.status}`, res.status);
  }
  return res.json();
}

async function sendJson(path: string, method: "POST" | "PUT", body: unknown): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(`${QUANT_API_URL}${path}`, {
      method,
      cache: "no-store",
      headers: { ...headers(), "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    throw new QuantApiError(`quant service unreachable at ${path}`, 503);
  }
  if (!res.ok) {
    throw new QuantApiError(`quant ${method} ${path} returned ${res.status}`, res.status);
  }
  return res.json();
}

// Signals query: optional sort ("net_edge" | "safety"), safe_only, min_net_edge.
export interface SignalsQuery {
  sort?: "net_edge" | "safety";
  safeOnly?: boolean;
  minNetEdge?: number;
}

export async function getSignals(query: SignalsQuery = {}): Promise<AdvisedSignal[]> {
  const params = new URLSearchParams();
  if (query.sort) params.set("sort", query.sort);
  if (query.safeOnly) params.set("safe_only", "true");
  if (query.minNetEdge !== undefined) params.set("min_net_edge", String(query.minNetEdge));
  const qs = params.toString();
  return AdvisedSignalListSchema.parse(await fetchJson(`/signals${qs ? `?${qs}` : ""}`));
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

export async function getConfig(): Promise<UserConfig> {
  return UserConfigSchema.parse(await fetchJson("/config"));
}

export async function updateConfig(config: UserConfig): Promise<UserConfig> {
  return UserConfigSchema.parse(await sendJson("/config", "PUT", config));
}

export async function getPositions(): Promise<PositionsResponse> {
  return PositionsResponseSchema.parse(await fetchJson("/positions"));
}

export async function createPosition(body: OpenPositionRequest): Promise<Position> {
  return PositionSchema.parse(await sendJson("/positions", "POST", body));
}
