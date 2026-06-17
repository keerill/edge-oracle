// Typed, server-side client for the executor's control API (Phase 6-UI). Zod-validated at the
// boundary (the executor is untrusted data like quant). Used only by the BFF route handlers
// (app/api/exec/*); never imported into client code (it reads EXEC_API_URL / EXEC_API_KEY).

import { EXEC_API_URL, EXEC_API_KEY } from "@/lib/env";
import {
  ApprovalResultSchema,
  PendingIntentListSchema,
  type ApprovalResult,
  type PendingIntent,
} from "@/lib/schemas/exec";

export class ExecApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ExecApiError";
  }
}

function headers(): Record<string, string> {
  return {
    accept: "application/json",
    ...(EXEC_API_KEY ? { "X-API-Key": EXEC_API_KEY } : {}),
  };
}

export async function getPendingIntents(): Promise<PendingIntent[]> {
  let res: Response;
  try {
    res = await fetch(`${EXEC_API_URL}/intents/pending`, { cache: "no-store", headers: headers() });
  } catch {
    throw new ExecApiError("executor service unreachable", 503);
  }
  if (!res.ok) throw new ExecApiError(`executor returned ${res.status}`, res.status);
  return PendingIntentListSchema.parse(await res.json());
}

export async function approveIntent(intentId: string): Promise<ApprovalResult> {
  const id = encodeURIComponent(intentId);
  let res: Response;
  try {
    res = await fetch(`${EXEC_API_URL}/intents/${id}/approve`, {
      method: "POST",
      cache: "no-store",
      headers: headers(),
    });
  } catch {
    throw new ExecApiError("executor service unreachable", 503);
  }
  if (!res.ok) throw new ExecApiError(`executor approve returned ${res.status}`, res.status);
  return ApprovalResultSchema.parse(await res.json());
}
