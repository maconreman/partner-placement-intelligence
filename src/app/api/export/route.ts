import { NextResponse } from "next/server";
import { exportToDrive } from "@/lib/driveExport";

export const runtime = "nodejs";
export const maxDuration = 120;

export async function POST(req: Request) {
  try {
    const { rows, filename, fmt } = await req.json();
    if (!Array.isArray(rows) || !rows.length) {
      return NextResponse.json({ error: "Nothing to export." }, { status: 400 });
    }
    const url = await exportToDrive(rows, filename || "FFG-Placements.xlsx", fmt === "csv" ? "csv" : "excel");
    return NextResponse.json({ url });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Export failed." },
      { status: 500 }
    );
  }
}
