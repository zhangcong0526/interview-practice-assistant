import asyncio
import shutil
import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from ..schemas import ExpressionAnalyzeRequest, ExpressionQuestionRequest
from ..services import asr, audio, expression as expression_service
from ..config import settings

router = APIRouter(prefix="/api/expression", tags=["expression"])

# 单题口述一般不超过 3 分钟，20MB 足够覆盖。
MAX_CLIP_BYTES = 20 * 1024 * 1024


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
            req.practice_mode,
        )
    except expression_service.ExpressionError as exc:
        raise HTTPException(422, str(exc))


@router.post("/transcribe")
async def transcribe_clip(file: UploadFile = File(...)):
    """把表达训练的短录音直接送本地 whisper 转写。

    浏览器 SpeechRecognition 会过滤语气词并把口语润色成书面语，
    会让口头禅、卡顿指标系统性失真，因此这里改走与上传录音相同的本地模型。
    """
    workdir = settings.data_dir / "expression" / "tmp" / uuid.uuid4().hex
    workdir.mkdir(parents=True, exist_ok=True)
    raw_path = workdir / "clip.webm"
    try:
        data = await file.read(MAX_CLIP_BYTES + 1)
        if not data:
            raise HTTPException(422, "没有收到音频数据。")
        if len(data) > MAX_CLIP_BYTES:
            raise HTTPException(422, "录音超过 20MB，请控制在 3 分钟以内。")
        raw_path.write_bytes(data)

        compressed = workdir / "compressed.wav"
        try:
            await audio.extract_for_asr_wav(raw_path, compressed)
            duration = await audio.probe_duration(compressed)
            result = await asyncio.to_thread(asr.transcribe_file, compressed)
        except audio.AudioError as exc:
            raise HTTPException(422, str(exc))
        except asr.AsrError as exc:
            raise HTTPException(422, f"本地语音识别失败：{exc}")

        text = (result.get("text") or "").strip()
        if not text:
            raise HTTPException(422, "没有识别到有效语音，请靠近麦克风重说一遍。")
        return {
            "text": text,
            "duration": round(duration, 1),
            "language": result.get("language", ""),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@router.get("/progress")
async def get_progress(limit: int = Query(default=30, ge=1, le=200)):
    return expression_service.progress()


@router.get("/sessions")
async def list_sessions(limit: int = Query(default=30, ge=1, le=200)):
    return {"sessions": expression_service.list_sessions(limit)}
