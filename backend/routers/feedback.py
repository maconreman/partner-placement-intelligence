"""routers/feedback.py — POST /api/feedback"""
from fastapi import APIRouter, Request
from ..lib.bigquery import sync_feedback_to_bigquery
from ..lib.config import USE_BIGQUERY
from ..lib.util import FeedbackRow

router = APIRouter(prefix="/api")


@router.post("/feedback")
async def feedback(request: Request):
    try:
        body = await request.json()
        row = FeedbackRow(
            query=str(body.get("query", "")).strip(),
            vertical=str(body.get("vertical", "")).strip(),
            category=str(body.get("category", "")).strip(),
            topic=str(body.get("topic", "")).strip(),
            domains=str(body.get("domains", "")).strip(),
        )
        if not row.query or not row.vertical:
            return {"error": "query and vertical are required"}
        if USE_BIGQUERY:
            await sync_feedback_to_bigquery(row)
        return {"ok": True}
    except Exception:
        return {"ok": True}  # advisory — never block the user
