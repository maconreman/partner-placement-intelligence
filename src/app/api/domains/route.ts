import { NextResponse } from "next/server";
import { listGscProperties } from "@/lib/gsc";
import { FFG_OWNED_DOMAINS, EXCLUDED_DOMAINS } from "@/lib/config";

export const runtime = "nodejs";
export const maxDuration = 60;

// Normalise a GSC property identifier to a bare hostname for exclusion matching.
// Strips the `sc-domain:` prefix, the URL scheme, any `www.` prefix, and trailing
// slashes / paths — so every variant of the same site collapses to one entry.
function toHost(siteUrl: string): string {
  let s = siteUrl.replace(/^sc-domain:/i, "").trim().toLowerCase();
  s = s.replace(/^https?:\/\//, "");
  s = s.replace(/^www\./, "");
  s = s.split("/")[0];
  return s;
}

export async function GET() {
  try {
    const { ordered } = await listGscProperties();
    const ffgSet = new Set(FFG_OWNED_DOMAINS);
    // Filter out any property whose hostname is in EXCLUDED_DOMAINS. The
    // exclusion happens server-side so excluded sites never reach the UI.
    const domains = ordered
      .filter((d) => !EXCLUDED_DOMAINS.has(toHost(d)))
      .map((d) => ({
        siteUrl: d,
        short: d.replace("sc-domain:", ""),
        isFfg: ffgSet.has(d),
      }));
    return NextResponse.json({ domains });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Failed to list GSC properties." },
      { status: 500 }
    );
  }
}
