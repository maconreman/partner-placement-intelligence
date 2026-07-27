"""routers/export.py — POST /api/export (Drive) + POST /api/download (direct)"""
from fastapi import APIRouter, Request, Response
from ..lib.drive_export import export_to_drive, build_xlsx

router = APIRouter(prefix="/api")


@router.post("/export")
async def export_to_drive_route(request: Request):
    body = await request.json()
    rows = body.get("rows", [])
    filename = body.get("filename", "FFG-Placements.xlsx")
    fmt = body.get("fmt", "excel")
    if not rows:
        return Response(content='{"error":"Nothing to export."}', status_code=400, media_type="application/json")
    try:
        url = await export_to_drive(rows, filename, "csv" if fmt == "csv" else "excel")
        return {"url": url}
    except Exception as e:
        return Response(content=f'{{"error":"{e}"}}', status_code=500, media_type="application/json")


@router.post("/download")
async def download(request: Request):
    body = await request.json()
    rows = body.get("rows", [])
    filename = str(body.get("filename", "FFG-Placements"))
    if not rows:
        return Response(content='{"error":"Nothing to download."}', status_code=400, media_type="application/json")
    try:
        buf = build_xlsx(rows)
        name = filename if filename.lower().endswith(".xlsx") else f"{filename}.xlsx"
        safe_name = name.replace('"', "")
        return Response(
            content=buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}"',
                "Content-Length": str(len(buf)),
            },
        )
    except Exception as e:
        return Response(content=f'{{"error":"{e}"}}', status_code=500, media_type="application/json")
