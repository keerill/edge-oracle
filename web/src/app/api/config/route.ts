import { NextResponse } from "next/server";
import { getConfig, updateConfig, QuantApiError } from "@/lib/api/client";
import { UserConfigSchema } from "@/lib/schemas/config";

// BFF: read/write the personal sizing & risk config. Keeps QUANT_API_URL server-side.
export async function GET() {
  try {
    return NextResponse.json(await getConfig());
  } catch (err) {
    const status = err instanceof QuantApiError ? err.status : 500;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "failed to load config" },
      { status },
    );
  }
}

export async function PUT(req: Request) {
  let parsed;
  try {
    parsed = UserConfigSchema.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "invalid config payload" }, { status: 422 });
  }
  try {
    return NextResponse.json(await updateConfig(parsed));
  } catch (err) {
    const status = err instanceof QuantApiError ? err.status : 500;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "failed to save config" },
      { status },
    );
  }
}
