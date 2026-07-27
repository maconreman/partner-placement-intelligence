// ─────────────────────────────────────────────────────────────────────────────
// middleware.ts  —  HTTP Basic Auth gate for the entire deployment
//
// Puts a single shared username/password in front of every route so only people
// who have the credentials can reach the app. Credentials live ONLY in the
// BASIC_AUTH_USER / BASIC_AUTH_PASS environment variables (Vercel encrypted env
// store) — never in the codebase, never logged, never returned in a response.
//
// Security guardrails:
//   1. Timing-safe credential comparison — a constant-time digest compare so an
//      attacker cannot infer the password from response-time differences.
//   2. Per-IP rate limiting via Upstash Redis — 5 failed attempts in a 10-minute
//      window triggers a 15-minute lockout (429). A correct login clears the
//      counter. Without this, Basic Auth has no brute-force protection.
//   3. HTTPS enforcement — rejects any request whose x-forwarded-proto is not
//      https (Vercel terminates TLS and sets this header). Basic Auth sends
//      credentials base64-encoded, not encrypted, so it is only safe over TLS.
//   4. Generic realm string ("Restricted") — avoids leaking what the protected
//      system is in the browser dialog and in any intermediary logs.
//   5. OAuth callback exemption — /api/auth/callback is left open so the Google
//      sign-in redirect can complete. Everything else is gated.
//
// Runs on the Edge runtime, so it uses Web Crypto (crypto.subtle) and the
// Upstash REST client (HTTP-based, Edge-compatible) — not Node's crypto module.
// ─────────────────────────────────────────────────────────────────────────────
import { NextRequest, NextResponse } from "next/server";
import { Redis } from "@upstash/redis";

// Rate-limit tuning.
const MAX_FAILS = 5;            // failures allowed within the window
const WINDOW_SECONDS = 600;     // 10-minute rolling window for counting failures
const LOCKOUT_SECONDS = 900;    // 15-minute block once MAX_FAILS is exceeded
const FAIL_PREFIX = "ffg:authfail:";
const LOCK_PREFIX = "ffg:authlock:";

// Lazily-created Upstash client. Rate limiting is best-effort: if Upstash is not
// configured, auth still works (fail-open on the limiter, never on the password).
let _redis: Redis | null = null;
function redis(): Redis | null {
  if (_redis) return _redis;
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) return null;
  _redis = new Redis({ url, token });
  return _redis;
}

// Client IP from Vercel's forwarded headers.
function clientIp(req: NextRequest): string {
  const xff = req.headers.get("x-forwarded-for");
  if (xff) return xff.split(",")[0].trim();
  return req.headers.get("x-real-ip") ?? "unknown";
}

// SHA-256 digest of a string → hex. Used to normalise both the supplied and the
// expected credential to fixed-length byte arrays before comparison, so the
// compare loop length never depends on the secret.
async function sha256(input: string): Promise<Uint8Array> {
  const data = new TextEncoder().encode(input);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return new Uint8Array(buf);
}

// Constant-time comparison of two equal-length byte arrays. Always iterates the
// full length and accumulates differences with bitwise OR — no early exit.
function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

// Compare "user:pass" against the configured credentials in constant time.
async function credentialsMatch(supplied: string): Promise<boolean> {
  const user = process.env.BASIC_AUTH_USER ?? "";
  const pass = process.env.BASIC_AUTH_PASS ?? "";
  // Digest both sides; comparing fixed-length digests keeps the compare length
  // independent of the credential length.
  const [suppliedDigest, expectedDigest] = await Promise.all([
    sha256(supplied),
    sha256(`${user}:${pass}`),
  ]);
  return timingSafeEqual(suppliedDigest, expectedDigest);
}

function unauthorized(): NextResponse {
  return new NextResponse("Authentication required.", {
    status: 401,
    headers: {
      // Generic realm — does not name the system.
      "WWW-Authenticate": 'Basic realm="Restricted", charset="UTF-8"',
    },
  });
}

function tooManyRequests(retryAfter: number): NextResponse {
  return new NextResponse("Too many failed attempts. Try again later.", {
    status: 429,
    headers: { "Retry-After": String(retryAfter) },
  });
}

export async function middleware(req: NextRequest) {
  // ── HTTPS enforcement ──────────────────────────────────────────────────────
  // Vercel sets x-forwarded-proto. In local dev (next dev) the header is absent,
  // so we only reject when it is explicitly "http".
  const proto = req.headers.get("x-forwarded-proto");
  if (proto === "http") {
    const httpsUrl = req.nextUrl.clone();
    httpsUrl.protocol = "https:";
    return NextResponse.redirect(httpsUrl, 308);
  }

  // If credentials are not configured, do not lock the team out — allow through.
  // (Set BASIC_AUTH_USER / BASIC_AUTH_PASS in Vercel to activate the gate.)
  if (!process.env.BASIC_AUTH_USER || !process.env.BASIC_AUTH_PASS) {
    return NextResponse.next();
  }

  const ip = clientIp(req);
  const r = redis();

  // ── Lockout check ────────────────────────────────────────────────────────
  if (r) {
    try {
      const locked = await r.get<number>(`${LOCK_PREFIX}${ip}`);
      if (locked) return tooManyRequests(LOCKOUT_SECONDS);
    } catch {
      /* limiter is best-effort — never block a real user on a Redis hiccup */
    }
  }

  // ── Credential check ───────────────────────────────────────────────────────
  const header = req.headers.get("authorization") ?? "";
  const [scheme, encoded] = header.split(" ");
  let ok = false;
  if (scheme === "Basic" && encoded) {
    // atob is available on the Edge runtime.
    let decoded = "";
    try {
      decoded = atob(encoded);
    } catch {
      decoded = "";
    }
    if (decoded) ok = await credentialsMatch(decoded);
  }

  if (ok) {
    // Successful login clears the failure counter for this IP.
    if (r) {
      try {
        await r.del(`${FAIL_PREFIX}${ip}`);
      } catch {
        /* ignore */
      }
    }
    return NextResponse.next();
  }

  // ── Failed attempt — increment counter, lock out if over threshold ─────────
  if (r) {
    try {
      const fails = await r.incr(`${FAIL_PREFIX}${ip}`);
      if (fails === 1) await r.expire(`${FAIL_PREFIX}${ip}`, WINDOW_SECONDS);
      if (fails >= MAX_FAILS) {
        await r.set(`${LOCK_PREFIX}${ip}`, 1, { ex: LOCKOUT_SECONDS });
        await r.del(`${FAIL_PREFIX}${ip}`);
        return tooManyRequests(LOCKOUT_SECONDS);
      }
    } catch {
      /* limiter is best-effort */
    }
  }

  return unauthorized();
}

// ── Route matcher ────────────────────────────────────────────────────────────
// Gate everything EXCEPT:
//   • /api/auth/callback — the Google OAuth redirect must complete unauthenticated
//   • Next.js internals (_next/static, _next/image) and the favicon — static
//     assets that carry no sensitive data and would break the login page styling
//     if gated.
export const config = {
  matcher: [
    "/((?!api/auth/callback|_next/static|_next/image|favicon.ico).*)",
  ],
};
