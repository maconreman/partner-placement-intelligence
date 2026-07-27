import { NextResponse } from "next/server";
import { newOAuthClient, ACCOUNT_SCOPES } from "@/lib/googleAuth";
import type { AccountKey } from "@/lib/tokenStore";

export const runtime = "nodejs";

// GET /api/auth/start?account=data|analytics
// Builds the Google consent URL for the requested account and redirects there.
// `state` carries the account key through to the callback.
export async function GET(req: Request) {
  const url = new URL(req.url);
  const account = url.searchParams.get("account") as AccountKey | null;
  if (account !== "data" && account !== "analytics") {
    return NextResponse.json(
      { error: "account must be 'data' or 'analytics'." },
      { status: 400 }
    );
  }

  const client = newOAuthClient();
  const authUrl = client.generateAuthUrl({
    access_type: "offline", // request a refresh token
    prompt: "consent", // force refresh_token issuance on re-consent
    include_granted_scopes: true,
    scope: ACCOUNT_SCOPES[account],
    state: account,
  });

  return NextResponse.redirect(authUrl);
}
