// ─────────────────────────────────────────────────────────────────────────────
// tokenStore.ts  —  Upstash Redis token store for interactive OAuth
//
// Replaces the notebook's local *_token.json files. After a one-time browser
// consent (see /api/auth/*), each account's access + refresh token is persisted
// here under `ffg:token:{account}`. googleAuth.ts reads these, auto-refreshes on
// expiry, and writes the refreshed token back via setToken().
//
// Storage is key→JSON. Upstash REST is serverless-native (no socket), so this
// works on Vercel functions. Requires UPSTASH_REDIS_REST_URL + _TOKEN env vars.
// ─────────────────────────────────────────────────────────────────────────────
import { Redis } from "@upstash/redis";

export interface StoredToken {
  access_token: string;
  refresh_token: string;
  expiry_date: number; // epoch ms
  scope: string;
  token_type: string;
}

export type AccountKey = "data" | "analytics";

const KEY_PREFIX = "ffg:token:";

let _redis: Redis | null = null;

function redis(): Redis {
  if (_redis) return _redis;
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) {
    throw new Error(
      "Upstash is not configured. Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN."
    );
  }
  _redis = new Redis({ url, token });
  return _redis;
}

export async function getToken(account: AccountKey): Promise<StoredToken | null> {
  // Upstash auto-deserializes JSON values, so this is already an object (or null).
  const raw = await redis().get<StoredToken>(`${KEY_PREFIX}${account}`);
  return raw ?? null;
}

export async function setToken(account: AccountKey, token: StoredToken): Promise<void> {
  await redis().set(`${KEY_PREFIX}${account}`, token);
}

export async function deleteToken(account: AccountKey): Promise<void> {
  await redis().del(`${KEY_PREFIX}${account}`);
}

export async function getAuthStatus(): Promise<Record<AccountKey, boolean>> {
  const [d, a] = await Promise.all([getToken("data"), getToken("analytics")]);
  return { data: Boolean(d?.refresh_token), analytics: Boolean(a?.refresh_token) };
}
