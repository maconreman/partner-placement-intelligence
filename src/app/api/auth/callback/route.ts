import { NextResponse } from "next/server";
import { newOAuthClient, appBaseUrl } from "@/lib/googleAuth";
import { setToken, type AccountKey } from "@/lib/tokenStore";

export const runtime = "nodejs";

// GET /api/auth/callback?code=...&state=data|analytics
// Exchanges the authorization code for tokens, persists them to Upstash under
// the account key carried in `state`, then redirects back to /setup.
export async function GET(req: Request) {
  const url = new URL(req.url);
  const code = url.searchParams.get("code");
  const account = url.searchParams.get("state") as AccountKey | null;
  const oauthError = url.searchParams.get("error");

  const back = (params: string) =>
    NextResponse.redirect(`${appBaseUrl()}/setup?${params}`);

  if (oauthError) return back(`error=${encodeURIComponent(oauthError)}`);
  if (!code) return back("error=missing_code");
  if (account !== "data" && account !== "analytics") {
    return back("error=bad_state");
  }

  try {
    const client = newOAuthClient();
    const { tokens } = await client.getToken(code);

    if (!tokens.refresh_token) {
      // Without a refresh token the connection can't survive token expiry.
      // This happens if the user previously consented; force re-consent.
      return back(`error=no_refresh_token&account=${account}`);
    }

    await setToken(account, {
      access_token: tokens.access_token ?? "",
      refresh_token: tokens.refresh_token,
      expiry_date: tokens.expiry_date ?? 0,
      scope: tokens.scope ?? "",
      token_type: tokens.token_type ?? "Bearer",
    });

    return back(`connected=${account}`);
  } catch (e) {
    const msg = e instanceof Error ? e.message : "token_exchange_failed";
    return back(`error=${encodeURIComponent(msg)}`);
  }
}
