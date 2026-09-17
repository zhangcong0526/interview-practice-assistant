import asyncio
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..schemas import ResumeFileRequest, ResumeProfileUpdate, ResumeTextRequest
from ..services import resume as resume_service
from ..services import resume_advisor
from .uploads import get_file_record

router = APIRouter(prefix="/api/resume", tags=["resume"])


def _public(record: dict) -> dict:
    return {
        "resume_id": record["resume_id"],
        "title": record.get("title", ""),
        "source_type": record.get("source_type", ""),
        "char_count": record.get("char_count", 0),
        "created_at": record.get("created_at", 0),
        "profile": record.get("profile") or {},
        "raw_text": record.get("raw_text", ""),
    }


@router.post("/files")
async def upload_resume(req: ResumeFileRequest):
    record = get_file_record(req.file_id)
    path = Path(record["path"])
    try:
        text = await asyncio.to_thread(
            resume_service.extract_resume_text, path, record["filename"]
        )
        # job_id 由正文内容决定：同一份简历重试时能接上上次的解析进度。
        job_id = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        profile = await asyncio.to_thread(resume_service.parse_resume, text, job_id)
    except resume_service.ResumeError as exc:
        raise HTTPException(422, str(exc))
    finally:
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))

    title = req.title.strip() or Path(record["filename"]).stem
    saved = await asyncio.to_thread(
        resume_service.save_resume, title, text, profile, "file"
    )
    return _public(saved)


@router.post("/text")
async def paste_resume(req: ResumeTextRequest):
    text = req.content.strip()
    if not text:
        raise HTTPException(422, "简历内容不能为空。")
    try:
        job_id = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        profile = await asyncio.to_thread(resume_service.parse_resume, text, job_id)
    except resume_service.ResumeError as exc:
        raise HTTPException(422, str(exc))
    saved = await asyncio.to_thread(
        resume_service.save_resume, req.title, text, profile, "paste"
    )
    return _public(saved)


@router.get("")
async def list_resumes():
    return resume_service.list_resumes()


@router.get("/active")
async def get_active():
    record = resume_service.get_active_resume()
    return _public(record) if record else None


@router.post("/active/advice")
async def generate_advice(refresh: bool = False):
    try:
        return await asyncio.to_thread(resume_advisor.generate_advice, "", refresh)
    except resume_advisor.AdvisorError as exc:
        raise HTTPException(422, str(exc))


@router.get("/{resume_id}/advice")
async def load_advice(resume_id: str):
    cached = resume_advisor.load_advice(resume_id)
    if not cached:
        raise HTTPException(404, "还没有生成过优化建议。")
    return cached


@router.get("/{resume_id}")
async def get_resume(resume_id: str):
    try:
        return _public(resume_service.get_resume(resume_id))
    except resume_service.ResumeError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{resume_id}/activate")
async def activate_resume(resume_id: str):
    try:
        await asyncio.to_thread(resume_service.set_active, resume_id)
    except resume_service.ResumeError as exc:
        raise HTTPException(404, str(exc))
    return {"resume_id": resume_id, "is_active": True}


@router.put("/{resume_id}/profile")
async def update_profile(resume_id: str, req: ResumeProfileUpdate):
    try:
        record = await asyncio.to_thread(
            resume_service.update_profile, resume_id, req.profile
        )
    except resume_service.ResumeError as exc:
        raise HTTPException(404, str(exc))
    return _public(record)


@router.delete("/{resume_id}")
async def delete_resume(resume_id: str):
    try:
        await asyncio.to_thread(resume_service.delete_resume, resume_id)
    except resume_service.ResumeError as exc:
        raise HTTPException(404, str(exc))
    return {"deleted": resume_id}
