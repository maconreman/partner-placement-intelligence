// POST /api/download
//
// Builds the XLSX in-memory and returns the bytes directly as a file download —
// no Google Drive upload, no OAuth required. Mirrors the export schema produced
// by exportToDrive() (both call buildXlsx).
//
// Body: { rows, filename }

import { buildXlsx } from "@/lib/driveExport";

export const runtime = "nodejs";
export const maxDuration = 120;

export async function POST(req: Request) {
  try {
    const { rows, filename } = await req.json();
    if (!Array.isArray(rows) || !rows.length) {
      return new Response(JSON.stringify({ error: "Nothing to download." }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    }
    const buf = await buildXlsx(rows);
    const name = String(filename || "FFG-Placements.xlsx").match(/\.xlsx$/i)
      ? String(filename)
      : `${filename || "FFG-Placements"}.xlsx`;

    return new Response(new Uint8Array(buf), {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": `attachment; filename="${name.replace(/"/g, "")}"`,
        "Content-Length": String(buf.length),
      },
    });
  } catch (e) {
    return new Response(
      JSON.stringify({ error: e instanceof Error ? e.message : "Download failed." }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
}
