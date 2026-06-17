import { NextResponse } from "next/server";
import { getPendingIntents, ExecApiError } from "@/lib/api/exec";

// BFF: list the executor's pending-approval intents. Keeps EXEC_API_URL server-side.
export async function GET() {
  try {
    return NextResponse.json(await getPendingIntents());
  } catch (err) {
    const status = err instanceof ExecApiError ? err.status : 500;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "failed to load pending intents" },
      { status },
    );
  }
}
