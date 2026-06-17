import { NextResponse } from "next/server";
import { getPositions, createPosition, QuantApiError } from "@/lib/api/client";
import { OpenPositionRequestSchema } from "@/lib/schemas/position";

// BFF: list portfolio positions (with live P&L) and record a placed bet.
export async function GET() {
  try {
    return NextResponse.json(await getPositions());
  } catch (err) {
    const status = err instanceof QuantApiError ? err.status : 500;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "failed to load positions" },
      { status },
    );
  }
}

export async function POST(req: Request) {
  let parsed;
  try {
    parsed = OpenPositionRequestSchema.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "invalid position payload" }, { status: 422 });
  }
  try {
    return NextResponse.json(await createPosition(parsed), { status: 201 });
  } catch (err) {
    const status = err instanceof QuantApiError ? err.status : 500;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "failed to record position" },
      { status },
    );
  }
}
