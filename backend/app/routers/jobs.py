import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..schemas import JobCreateResponse, TranscribeRequest
from ..services import asr, audio
from .uploads import get_file_record

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

RESULTS_DIR = settings.data_dir / "results"
WORK_DIR = settings.data_dir / "work"

_jobs: dict[str, dict] = {}


def _set(job_id: str, **kw) -> None:
    _jobs[job_id].update(kw)


@router.post("", response_model=JobCreateResponse)
async def create_job(req: TranscribeRequest):
    get_file_record(req.file_id)
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "job_id": job_id,
        "file_id": req.file_id,
        "status": "queued",
        "stage": "排队中",
        "progress": 0.0,
        "created": time.time(),
    }
    asyncio.create_task(_run_transcribe(job_id, req.file_id))
    return JobCreateResponse(job_id=job_id)


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if job:
        return job
    result_path = RESULTS_DIR / f"{job_id}.json"
    if result_path.exists():
        return {
            "job_id": job_id,
            "status": "done",
            "progress": 1.0,
            "result": json.loads(result_path.read_text(encoding="utf-8")),
        }
    raise HTTPException(404, "任务不存在")


async def _run_transcribe(job_id: str, file_id: str) -> None:
    workdir = WORK_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        record = get_file_record(file_id)
        src = Path(record["path"])

        _set(job_id, status="running", stage="音频压缩", progress=0.05)
        compressed = await audio.compress_for_asr(src, workdir / "compressed.webm")

        _set(job_id, stage="分片处理", progress=0.15)
        segments = await audio.split_if_needed(compressed, settings.whisper_max_bytes, workdir / "segments")

        texts: list[str] = []
        all_segments: list[dict] = []
        for i, (seg_path, offset) in enumerate(segments):
            _set(
                job_id,
                stage=f"语音转写 {i + 1}/{len(segments)}",
                progress=0.2 + 0.7 * (i / max(len(segments), 1)),
            )
            data = await asyncio.to_thread(asr.transcribe_file, seg_path)
            text = (data.get("text") or "").strip()
            if text:
                texts.append(text)
            for seg in data.get("segments") or []:
                all_segments.append(
                    {
                        "start": round(offset + float(seg.get("start", 0)), 2),
                        "end": round(offset + float(seg.get("end", 0)), 2),
                        "text": (seg.get("text") or "").strip(),
                    }
                )
            _set(job_id, progress=0.2 + 0.7 * ((i + 1) / max(len(segments), 1)))

        _set(job_id, stage="整理结果", progress=0.95)
        duration = await audio.probe_duration(compressed)
        result = {
            "text": asr.join_transcripts(texts),
            "segments": all_segments,
            "duration": round(duration, 2),
            "original_size": record["size"],
            "compressed_size": compressed.stat().st_size,
            "segment_count": len(segments),
        }
        (RESULTS_DIR / f"{job_id}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
        _set(job_id, status="done", stage="完成", progress=1.0, result=result)
    except Exception as exc:
        _set(job_id, status="error", error=str(exc))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
