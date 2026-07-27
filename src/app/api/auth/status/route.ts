import { NextResponse } from "next/server";
import { getAuthStatus } from "@/lib/tokenStore";

export const runtime = "nodejs";

// GET /api/auth/status → { data: boolean, analytics: boolean, ready: boolean }
// `ready` is true only when both accounts have a stored refresh token.
export async function GET() {
  try {
    const status = await getAuthStatus();
    return NextResponse.json({
      ...status,
      ready: status.data && status.analytics,
    });
  } catch (e) {
    return NextResponse.json(
      {
        data: false,
        analytics: false,
        ready: false,
        error: e instanceof Error ? e.message : "status_check_failed",
      },
      { status: 500 }
    );
  }
}
