import { NextResponse } from "next/server";
import { getSignal, QuantApiError } from "@/lib/api/client";

// BFF: proxy the quant /signals/{id} detail endpoint (full sizing breakdown + cost gate).
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    return NextResponse.json(await getSignal(id));
  } catch (err) {
    const status = err instanceof QuantApiError ? err.status : 500;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "failed to load signal" },
      { status },
    );
  }
}
