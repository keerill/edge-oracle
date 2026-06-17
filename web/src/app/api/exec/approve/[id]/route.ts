import { NextResponse } from "next/server";
import { approveIntent, ExecApiError } from "@/lib/api/exec";

// BFF: approve a pending intent (executor signs + dry-run submits). Keeps EXEC_API_URL server-side.
export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    return NextResponse.json(await approveIntent(id));
  } catch (err) {
    const status = err instanceof ExecApiError ? err.status : 500;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "failed to approve intent" },
      { status },
    );
  }
}
