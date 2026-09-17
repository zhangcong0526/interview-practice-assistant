from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from ..schemas import TTSRequest
from ..services import tts as tts_service

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/voices")
async def list_voices():
    return {"voices": tts_service.list_voices(), "default": tts_service.DEFAULT_VOICE}


@router.post("/speak")
async def speak(req: TTSRequest):
    try:
        audio = await tts_service.synthesise(req.text, req.voice, req.rate, req.pitch)
    except tts_service.TTSError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"语音合成失败：{type(exc).__name__}: {exc}")
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/stream")
async def speak_stream(req: TTSRequest):
    """边合成边下发，让前端不必等整段音频就能起播。"""
    # edge-tts 走公网 websocket，偶发首次握手失败。
    # 首块拿不到时回落到带重试的整段合成，保证面试不会因为一次抖动而中断。
    try:
        iterator = tts_service.stream(req.text, req.voice, req.rate, req.pitch)
        first = await iterator.__anext__()
    except Exception:  # noqa: BLE001
        return await speak(req)

    async def body():
        yield first
        try:
            async for chunk in iterator:
                yield chunk
        except Exception:  # noqa: BLE001
            # 响应头已发出，无法改状态码；中断后前端会按已收到的音频播放。
            return

    return StreamingResponse(
        body(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
