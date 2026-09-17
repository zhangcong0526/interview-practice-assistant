import asyncio
import json
from pathlib import Path


class AudioError(RuntimeError):
    pass


async def _run(cmd: list[str]) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise AudioError("未找到 ffmpeg 或 ffprobe，请安装后确保其在 PATH 中。") from exc
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-800:]
        raise AudioError(f"命令执行失败: {' '.join(cmd[:2])} ...\n{tail}")
    return stdout.decode(errors="replace")


async def probe_duration(path: Path) -> float:
    out = await _run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ]
    )
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise AudioError(f"无法读取音频时长: {path.name}") from exc


async def compress_for_asr(src: Path, dst: Path) -> Path:
    """16kHz 单声道 24kbps Opus 对语音识别足够，且能把数小时无损音频压到几 MB。"""
    await _run(
        [
            "ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libopus", "-b:a", "24k", str(dst),
        ]
    )
    return dst


async def split_if_needed(
    path: Path, max_bytes: int, workdir: Path, segment_seconds: int = 600
) -> list[tuple[Path, float]]:
    """超过大小上限时按固定时长切片，返回 (分片路径, 在原音频中的起始秒)。"""
    if path.stat().st_size <= max_bytes:
        return [(path, 0.0)]
    workdir.mkdir(parents=True, exist_ok=True)
    pattern = workdir / "seg%04d.webm"
    await _run(
        [
            "ffmpeg", "-y", "-i", str(path), "-c", "copy",
            "-f", "segment", "-segment_time", str(segment_seconds),
            "-reset_timestamps", "1", str(pattern),
        ]
    )
    segments = sorted(workdir.glob("seg*.webm"))
    result: list[tuple[Path, float]] = []
    offset = 0.0
    for seg in segments:
        duration = await probe_duration(seg)
        # ffmpeg 在整段刚好落在切片边界时可能输出一个 0 秒尾部文件。
        if duration <= 0.05:
            seg.unlink(missing_ok=True)
            continue
        result.append((seg, offset))
        offset += duration
    return result
