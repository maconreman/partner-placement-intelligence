// ─────────────────────────────────────────────────────────────────────────────
// driveExport.ts  —  Port of export_to_drive() (CELL 4)
//
// Build XLSX, apply red→white→green color scales to the Topic Relevance and SEO
// Score columns (openpyxl ColorScaleRule → exceljs conditional formatting), then
// upload to the "FFG Universe" Drive folder. CSV path is supported too.
// ─────────────────────────────────────────────────────────────────────────────
import ExcelJS from "exceljs";
import { Readable } from "stream";
import { DRIVE_EXPORT_FOLDER, EXPORT_LABELS } from "./config";
import { driveClient } from "./googleAuth";

type Row = Record<string, string | number>;

async function getOrCreateFolder(name: string): Promise<string> {
  const drive = await driveClient();
  const q = `name='${name.replace(/'/g, "\\'")}' and mimeType='application/vnd.google-apps.folder' and trashed=false`;
  const res = await drive.files.list({ q, fields: "files(id)", supportsAllDrives: true, includeItemsFromAllDrives: true });
  if (res.data.files?.length) return res.data.files[0].id!;
  const created = await drive.files.create({
    requestBody: { name, mimeType: "application/vnd.google-apps.folder" },
    fields: "id", supportsAllDrives: true,
  });
  return created.data.id!;
}

export async function buildXlsx(rows: Row[]): Promise<Buffer> {
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet("Placements");
  if (!rows.length) return Buffer.from(await wb.xlsx.writeBuffer());

  const headers = Object.keys(rows[0]);
  ws.addRow(headers);
  for (const r of rows) ws.addRow(headers.map((h) => r[h]));

  const colLetter = (label: string): string | null => {
    const i = headers.indexOf(label);
    return i < 0 ? null : ws.getColumn(i + 1).letter;
  };
  const lastRow = ws.rowCount;
  // Red (8B0000) → white → green (006400) color scale, mid at 50th percentile.
  const scaleRule: any = {
    type: "colorScale",
    cfvo: [{ type: "min" }, { type: "percentile", value: 50 }, { type: "max" }],
    color: [{ argb: "FF8B0000" }, { argb: "FFFFFFFF" }, { argb: "FF006400" }],
  };
  for (const label of [EXPORT_LABELS.relevance, EXPORT_LABELS.seo]) {
    const c = colLetter(label);
    if (c && lastRow > 1) {
      ws.addConditionalFormatting({ ref: `${c}2:${c}${lastRow}`, rules: [scaleRule] });
    }
  }
  return Buffer.from(await wb.xlsx.writeBuffer());
}

export async function exportToDrive(rows: Row[], filename: string, fmt: "excel" | "csv" = "excel"): Promise<string> {
  const isCsv = fmt === "csv" || filename.toLowerCase().endsWith(".csv");
  const name = filename.match(/\.(xlsx|csv)$/i) ? filename : `${filename}.xlsx`;

  let body: Buffer;
  let mimeType: string;
  if (isCsv) {
    const headers = rows.length ? Object.keys(rows[0]) : [];
    const esc = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
    const lines = [headers.join(","), ...rows.map((r) => headers.map((h) => esc(r[h])).join(","))];
    body = Buffer.from(lines.join("\n"), "utf-8");
    mimeType = "text/csv";
  } else {
    body = await buildXlsx(rows);
    mimeType = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  }

  const folderId = await getOrCreateFolder(DRIVE_EXPORT_FOLDER);
  const drive = await driveClient();
  const uploaded = await drive.files.create({
    requestBody: { name, parents: [folderId] },
    media: { mimeType, body: Readable.from(body) },
    fields: "id,webViewLink",
    supportsAllDrives: true,
  });
  return uploaded.data.webViewLink ?? "";
}
