import asyncio

from fastapi import APIRouter, HTTPException, Query

from ..schemas import ExpressionAnalyzeRequest, ExpressionQuestionRequest
from ..services import expression as expression_service

router = APIRouter(prefix="/api/expression", tags=["expression"])


@router.post("/question")
async def next_question(req: ExpressionQuestionRequest):
    try:
        return await asyncio.to_thread(expression_service.drill_question, req.role_key)
    except expression_service.ExpressionError as exc:
        raise HTTPException(422, str(exc))


@router.post("/analyze")
async def analyze(req: ExpressionAnalyzeRequest):
    try:
        return await asyncio.to_thread(
            expression_service.analyze,
            req.role_key,
            req.question,
            req.question_label,
            req.transcript,
            req.duration_sec,
        )
    except expression_service.ExpressionError as exc:
        raise HTTPException(422, str(exc))


@router.get("/progress")
async def get_progress(limit: int = Query(default=30, ge=1, le=200)):
    return expression_service.progress()


@router.get("/sessions")
async def list_sessions(limit: int = Query(default=30, ge=1, le=200)):
    return {"sessions": expression_service.list_sessions(limit)}
