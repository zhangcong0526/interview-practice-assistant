import asyncio

from fastapi import APIRouter, HTTPException

from ..services import llm as llm_service
from ..services import resume as resume_service
from .. import prompts
from ..schemas import AnalyzeRequest

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


@router.post("")
async def analyze(req: AnalyzeRequest):
    if not req.transcript.strip():
        raise HTTPException(400, "转写文本不能为空")
    resume_profile = resume_service.resolve_profile_text(req.resume_id)
    user = prompts.build_analysis_user(
        req.jd, req.resume, req.transcript, req.knowledge, resume_profile
    )
    try:
        analysis = await asyncio.to_thread(llm_service.chat_json, prompts.ANALYSIS_SYSTEM, user)
    except llm_service.LlmError as exc:
        raise HTTPException(502, str(exc))
    if not isinstance(analysis.get("questions"), list):
        raise HTTPException(502, "LLM 返回结构异常（缺少 questions），请重试。")
    return analysis
