import { NextResponse } from "next/server";
import { getSignals, QuantApiError } from "@/lib/api/client";

// BFF: proxy the quant /signals endpoint. Keeps QUANT_API_URL server-side and presents a
// same-origin, already-validated payload to the client Signals page. (Phase 4 adds /api/stream.)
export async function GET() {
  try {
    return NextResponse.json(await getSignals());
  } catch (err) {
    const status = err instanceof QuantApiError ? err.status : 500;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "failed to load signals" },
      { status: status === 404 ? 502 : status },
    );
  }
}
