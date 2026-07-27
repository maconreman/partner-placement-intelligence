// POST /api/feedback
//
// Persists a single feedback submission to BigQuery (`feedback` table) — the
// sole feedback store. The Google Sheets write has been retired.
//
// Body: { query, vertical, category, topic, domains }
// The write is awaited so the user only sees success once it lands. Failures
// are logged server-side but still return ok — a flag is advisory, not critical.

import { NextRequest, NextResponse } from "next/server";
import { syncFeedbackToBigQuery } from "@/lib/bigquery";
import { USE_BIGQUERY } from "@/lib/config";
import { FeedbackRow } from "@/lib/util";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const row: FeedbackRow = {
      query:    String(body.query    ?? "").trim(),
      vertical: String(body.vertical ?? "").trim(),
      category: String(body.category ?? "").trim(),
      topic:    String(body.topic    ?? "").trim(),
      domains:  String(body.domains  ?? "").trim(),
    };

    if (!row.query || !row.vertical) {
      return NextResponse.json({ error: "query and vertical are required" }, { status: 400 });
    }

    if (USE_BIGQUERY) {
      await syncFeedbackToBigQuery(row);
    } else {
      console.warn("[/api/feedback] BigQuery not configured — feedback not persisted:", row);
    }

    return NextResponse.json({ ok: true });
  } catch (e) {
    console.error("[/api/feedback]", e);
    return NextResponse.json({ ok: true }); // advisory — never block the user
  }
}
