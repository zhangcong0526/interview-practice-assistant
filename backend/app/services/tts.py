"""基于 edge-tts 的中文神经语音合成。

浏览器内置 speechSynthesis 在 Windows 上只能拿到 SAPI 老引擎（Huihui 等），
机械感很重。这里改用微软 Edge 的在线神经语音，音色接近真人，且无需 API Key。
"""

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator

from ..config import settings


class TTSError(RuntimeError):
    pass


CACHE_DIR = settings.data_dir / "tts"
CACHE_LIMIT = 300
MAX_TEXT_CHARS = 1200

# 面试官场景优先选沉稳、专业的音色，避免过于活泼的播音腔。
VOICES: list[dict[str, str]] = [
    {
        "id": "zh-CN-YunyangNeural",
        "label": "云扬（男声·沉稳专业）",
        "gender": "male",
        "desc": "语速平稳、咬字清晰，最接近正式面试官",
    },
    {
        "id": "zh-CN-XiaoxiaoNeural",
        "label": "晓晓（女声·温和自然）",
        "gender": "female",
        "desc": "亲和力强，适合 HR 面或压力较低的场景",
    },
    {
        "id": "zh-CN-YunjianNeural",
        "label": "云健（男声·浑厚有力）",
        "gender": "male",
        "desc": "气场偏强，适合模拟压力面试",
    },
    {
        "id": "zh-CN-XiaoyiNeural",
        "label": "晓伊（女声·轻快）",
        "gender": "female",
        "desc": "节奏偏快，适合快问快答演练",
    },
    {
        "id": "zh-CN-YunxiNeural",
        "label": "云希（男声·年轻）",
        "gender": "male",
        "desc": "偏年轻的同事口吻，适合技术交流式面试",
    },
]

DEFAULT_VOICE = VOICES[0]["id"]
VOICE_IDS = {item["id"] for item in VOICES}


def list_voices() -> list[dict[str, str]]:
    return VOICES


def _clamp_percent(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _cache_key(text: str, voice: str, rate: int, pitch: int) -> str:
    raw = f"{voice}|{rate}|{pitch}|{text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _prune_cache() -> None:
    """缓存文件数超限时淘汰最旧的一批，避免无限增长。"""
    try:
        files = sorted(CACHE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for stale in files[:-CACHE_LIMIT]:
        stale.unlink(missing_ok=True)


def _normalise(text: str, voice: str, rate: int, pitch: int) -> tuple[str, str, int, int]:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        raise TTSError("待合成的文本为空。")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
    voice = voice if voice in VOICE_IDS else DEFAULT_VOICE
    return text, voice, _clamp_percent(rate, -50, 50), _clamp_percent(pitch, -50, 50)


def _cached_path(text: str, voice: str, rate: int, pitch: int):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{_cache_key(text, voice, rate, pitch)}.mp3"


async def stream(text: str, voice: str = "", rate: int = 0, pitch: int = 0) -> AsyncIterator[bytes]:
    """边合成边下发。

    整段合成要等 2 秒以上，而 edge-tts 的首个音频块 1 秒内就能到。
    切换岗位后开场白的等待时间主要花在这里，所以流式下发能明显缩短起播延迟。
    命中缓存时直接整段吐出，不再走公网。
    """
    text, voice, rate, pitch = _normalise(text, voice, rate, pitch)
    cached = _cached_path(text, voice, rate, pitch)
    if cached.exists() and cached.stat().st_size > 0:
        yield cached.read_bytes()
        return

    import edge_tts

    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=f"{rate:+d}%",
        pitch=f"{pitch:+d}Hz",
    )
    buffer = bytearray()
    try:
        async for chunk in communicate.stream():
            if chunk["type"] != "audio":
                continue
            data = chunk["data"]
            buffer.extend(data)
            yield data
    except Exception as exc:  # noqa: BLE001
        # 流式响应的头已经发出去了，无法再改状态码；
        # 已经推出去的片段仍可播放，剩下的交给前端的整段接口兜底。
        if not buffer:
            raise TTSError(f"语音合成失败：{type(exc).__name__}: {exc}") from exc
        return

    if buffer:
        # 落盘后下一次同样的文本可以秒回。
        cached.write_bytes(bytes(buffer))
        _prune_cache()


async def _synthesise(text: str, voice: str, rate: int, pitch: int) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=f"{rate:+d}%",
        pitch=f"{pitch:+d}Hz",
    )
    buffer = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.extend(chunk["data"])
    return bytes(buffer)


async def synthesise(text: str, voice: str = "", rate: int = 0, pitch: int = 0) -> bytes:
    text, voice, rate, pitch = _normalise(text, voice, rate, pitch)
    cached = _cached_path(text, voice, rate, pitch)
    if cached.exists() and cached.stat().st_size > 0:
        return cached.read_bytes()

    # edge-tts 走公网 websocket，偶发超时，重试两次。
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            audio = await _synthesise(text, voice, rate, pitch)
            if audio:
                cached.write_bytes(audio)
                _prune_cache()
                return audio
            last_error = TTSError("语音服务返回了空音频。")
        except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
            last_error = exc
        if attempt < 2:
            await asyncio.sleep(0.8 * (attempt + 1))

    raise TTSError(f"语音合成失败：{type(last_error).__name__}: {last_error}")
