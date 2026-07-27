// ─────────────────────────────────────────────────────────────────────────────
// googleAuth.ts  —  Interactive-OAuth replacement for CELL 2/3 auth
//
// The notebook used InstalledAppFlow.run_local_server() (browser + local token
// files), which cannot run on serverless. This build keeps interactive OAuth but
// persists tokens in Upstash (see tokenStore.ts) so they survive across stateless
// function invocations.
//
// Flow:
//   1. One-time consent per account via /api/auth/start → /api/auth/callback,
//      which writes {access,refresh} tokens to Upstash.
//   2. Each pipeline call builds an OAuth2 client from the stored refresh token.
//      google-auth-library auto-refreshes the access token on expiry and emits a
//      'tokens' event; we persist the refreshed token back to Upstash.
//
// Scope split mirrors the notebook: the data@ account holds GSC + Drive + Sheets;
// the analytics@ account holds GSC only. Drive/Sheets clients always use data@.
// ─────────────────────────────────────────────────────────────────────────────
import { google } from "googleapis";
import { OAuth2Client } from "google-auth-library";
import { getToken, setToken, type AccountKey } from "./tokenStore";

const GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"];
const DRIVE_SCOPES = [
  "https://www.googleapis.com/auth/drive",
  "https://www.googleapis.com/auth/spreadsheets",
];

// Per-account consent scopes. data@ is the Drive/Sheets owner, so it needs all
// three; analytics@ is a GSC-only identity for the analytics@ properties.
export const ACCOUNT_SCOPES: Record<AccountKey, string[]> = {
  data: [...GSC_SCOPES, ...DRIVE_SCOPES],
  analytics: [...GSC_SCOPES],
};

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set.`);
  return v;
}

export function appBaseUrl(): string {
  // Stable, explicit base URL. On Vercel set this to your production alias
  // (e.g. https://ffg-universe.vercel.app); locally http://localhost:3000.
  return requireEnv("APP_BASE_URL").replace(/\/+$/, "");
}

export function redirectUri(): string {
  return `${appBaseUrl()}/api/auth/callback`;
}

// Bare OAuth2 client (no credentials) — used by the consent + callback routes.
export function newOAuthClient(): OAuth2Client {
  return new google.auth.OAuth2(
    requireEnv("GOOGLE_CLIENT_ID"),
    requireEnv("GOOGLE_CLIENT_SECRET"),
    redirectUri()
  );
}

// Build a credentialed client from the stored token, with refresh write-back.
async function getAuthedClient(account: AccountKey): Promise<OAuth2Client> {
  const stored = await getToken(account);
  if (!stored?.refresh_token) {
    throw new Error(
      `Account ${account}@ is not connected. Open /setup and connect it before running.`
    );
  }
  const client = newOAuthClient();
  client.setCredentials({
    access_token: stored.access_token,
    refresh_token: stored.refresh_token,
    expiry_date: stored.expiry_date,
    scope: stored.scope,
    token_type: stored.token_type,
  });

  // Persist refreshed tokens. Google may omit refresh_token on refresh, so we
  // fall back to the existing one to avoid losing it.
  client.on("tokens", (t) => {
    void setToken(account, {
      access_token: t.access_token ?? stored.access_token,
      refresh_token: t.refresh_token ?? stored.refresh_token,
      expiry_date: t.expiry_date ?? stored.expiry_date,
      scope: t.scope ?? stored.scope,
      token_type: t.token_type ?? stored.token_type,
    });
  });

  return client;
}

// GSC client per account key (honors the data@/analytics@ split).
export async function gscClient(accountKey: string) {
  const auth = await getAuthedClient(accountKey as AccountKey);
  return google.searchconsole({ version: "v1", auth });
}

// Drive + Sheets always use the data@ identity (the Drive/Sheets owner).
export async function driveClient() {
  const auth = await getAuthedClient("data");
  return google.drive({ version: "v3", auth });
}

export async function sheetsClient() {
  const auth = await getAuthedClient("data");
  return google.sheets({ version: "v4", auth });
}
