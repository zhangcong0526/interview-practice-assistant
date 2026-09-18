import asyncio

from fastapi import APIRouter, HTTPException, Query

from ..schemas import (
    MistakeQuizRequest,
    QuizGenerateRequest,
    QuizSubmitRequest,
    TopicExtractRequest,
)
from ..services import quiz as quiz_service

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


@router.post("/topics")
async def extract_topics(req: TopicExtractRequest):
    try:
        topics = await asyncio.to_thread(
            quiz_service.extract_topics, req.doc_ids, req.refresh
        )
    except quiz_service.QuizError as exc:
        raise HTTPException(422, str(exc))
    return {"topics": topics}


@router.post("/papers")
async def generate_paper(req: QuizGenerateRequest):
    try:
        paper = await asyncio.to_thread(
            quiz_service.generate_paper,
            req.keywords,
            req.doc_ids,
            req.single,
            req.multiple,
            req.judge,
            req.difficulty,
            req.focus_weak,
        )
    except quiz_service.QuizError as exc:
        raise HTTPException(422, str(exc))
    return quiz_service.get_paper(paper["paper_id"])


@router.post("/papers/from-mistakes")
async def generate_mistake_paper(req: MistakeQuizRequest):
    try:
        paper = await asyncio.to_thread(
            quiz_service.generate_mistake_paper,
            req.limit,
            req.difficulty,
            req.scope,
            req.attempt_id,
        )
    except quiz_service.QuizError as exc:
        raise HTTPException(422, str(exc))
    return quiz_service.get_paper(paper["paper_id"])


@router.get("/papers")
async def list_papers(limit: int = Query(default=20, ge=1, le=100)):
    return quiz_service.list_papers(limit)


@router.get("/attempts")
async def list_attempts(limit: int = Query(default=20, ge=1, le=100)):
    return quiz_service.list_attempts(limit)


@router.get("/attempts/{attempt_id}")
async def get_attempt(attempt_id: str):
    try:
        return quiz_service.get_attempt(attempt_id)
    except quiz_service.QuizError as exc:
        raise HTTPException(404, str(exc))


@router.get("/mistakes")
async def list_mistakes(limit: int = Query(default=100, ge=1, le=500)):
    return quiz_service.list_mistakes(limit)


@router.delete("/mistakes/{key}")
async def delete_mistake(key: str):
    try:
        await asyncio.to_thread(quiz_service.delete_mistake, key)
    except quiz_service.QuizError as exc:
        raise HTTPException(404, str(exc))
    return {"deleted": key}


@router.get("/progress")
async def get_progress():
    return quiz_service.get_progress()


@router.post("/submit")
async def submit(req: QuizSubmitRequest):
    try:
        return await asyncio.to_thread(quiz_service.grade, req.paper_id, req.answers)
    except quiz_service.QuizError as exc:
        raise HTTPException(422, str(exc))


@router.get("/papers/{paper_id}")
async def get_paper(paper_id: str):
    try:
        return quiz_service.get_paper(paper_id)
    except quiz_service.QuizError as exc:
        raise HTTPException(404, str(exc))
