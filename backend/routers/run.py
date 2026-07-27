"""
routers/run.py — POST /api/run
NDJSON streaming pipeline. No timeout — FastAPI + HF Spaces has no ceiling.
"""
import asyncio
import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from ..lib.pipeline import run_pipeline

router = APIRouter(prefix="/api")


@router.post("/run")
async def run(request: Request):
    body = await request.json()
    domains = body.get("domains", [])
    topic = str(body.get("topic", "")).strip()
    start_date = body.get("startDate", "")
    end_date = body.get("endDate", "")

    if not isinstance(domains, list) or not domains:
        return StreamingResponse(
            iter([json.dumps({"type": "error", "code": "bad_request", "message": "Select at least one domain."}) + "\n"]),
            media_type="application/x-ndjson",
        )
    if not topic:
        return StreamingResponse(
            iter([json.dumps({"type": "error", "code": "bad_request", "message": "Enter a topic before running."}) + "\n"]),
            media_type="application/x-ndjson",
        )

    queue: asyncio.Queue = asyncio.Queue()

    async def pipeline_task():
        try:
            await run_pipeline(domains, topic, start_date, end_date, lambda e: queue.put_nowait(e))
        except Exception as exc:
            queue.put_nowait({"type": "error", "code": "hf_error", "message": str(exc)})
        finally:
            queue.put_nowait(None)  # sentinel

    asyncio.create_task(pipeline_task())

    async def event_stream():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield json.dumps(event) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
