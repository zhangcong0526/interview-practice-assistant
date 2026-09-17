import asyncio
import json
import math
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..schemas import UploadCompleteResponse, UploadInitRequest, UploadInitResponse

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

UPLOADS_DIR = settings.data_dir / "uploads"
FILES_DIR = settings.data_dir / "files"


def _ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)


def _load_meta(upload_id: str) -> dict:
    meta_path = UPLOADS_DIR / upload_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(404, "上传会话不存在或已过期")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def get_file_record(file_id: str) -> dict:
    _ensure_dirs()
    record_path = FILES_DIR / f"{file_id}.json"
    if not record_path.exists():
        raise HTTPException(404, "文件不存在")
    return json.loads(record_path.read_text(encoding="utf-8"))


@router.post("/init", response_model=UploadInitResponse)
async def init_upload(req: UploadInitRequest):
    _ensure_dirs()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if req.total_size > max_bytes:
        raise HTTPException(413, f"文件超过大小上限（{settings.max_upload_size_mb} MB）")
    chunk_size = settings.upload_chunk_mb * 1024 * 1024
    total_chunks = math.ceil(req.total_size / chunk_size)
    upload_id = uuid.uuid4().hex
    (UPLOADS_DIR / upload_id).mkdir(parents=True)
    meta = {
        "filename": req.filename,
        "total_size": req.total_size,
        "content_type": req.content_type,
        "chunk_size": chunk_size,
    }
    (UPLOADS_DIR / upload_id / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return UploadInitResponse(upload_id=upload_id, chunk_size=chunk_size, total_chunks=total_chunks)


@router.put("/{upload_id}/chunks/{index}")
async def upload_chunk(upload_id: str, index: int, request: Request):
    meta = _load_meta(upload_id)
    chunk_size = meta["chunk_size"]
    total_chunks = math.ceil(meta["total_size"] / chunk_size)
    if not 0 <= index < total_chunks:
        raise HTTPException(400, f"分片序号越界: {index}")
    part_path = UPLOADS_DIR / upload_id / f"{index:06d}.part"
    size = 0
    with part_path.open("wb") as f:
        async for data in request.stream():
            f.write(data)
            size += len(data)
    if index < total_chunks - 1:
        expected = chunk_size
    else:
        expected = meta["total_size"] - chunk_size * (total_chunks - 1)
    if size != expected:
        part_path.unlink(missing_ok=True)
        raise HTTPException(400, f"分片大小不符：收到 {size} 字节，应为 {expected} 字节")
    return {"received": index, "size": size}


def _assemble(upload_dir: Path, total_chunks: int, final_path: Path) -> None:
    with final_path.open("wb") as out:
        for i in range(total_chunks):
            part = upload_dir / f"{i:06d}.part"
            if not part.exists():
                raise HTTPException(400, f"缺少分片 {i}，请重新上传该文件")
            with part.open("rb") as f:
                shutil.copyfileobj(f, out)


@router.post("/{upload_id}/complete", response_model=UploadCompleteResponse)
async def complete_upload(upload_id: str):
    meta = _load_meta(upload_id)
    upload_dir = UPLOADS_DIR / upload_id
    total_chunks = math.ceil(meta["total_size"] / meta["chunk_size"])
    final_path = FILES_DIR / upload_id
    try:
        await asyncio.to_thread(_assemble, upload_dir, total_chunks, final_path)
        actual = final_path.stat().st_size
        if actual != meta["total_size"]:
            raise HTTPException(400, f"文件大小校验失败：应为 {meta['total_size']} 字节，实际 {actual} 字节")
    except Exception:
        final_path.unlink(missing_ok=True)
        raise
    record = {
        "filename": meta["filename"],
        "size": actual,
        "content_type": meta.get("content_type", ""),
        "path": str(final_path),
    }
    (FILES_DIR / f"{upload_id}.json").write_text(json.dumps(record), encoding="utf-8")
    shutil.rmtree(upload_dir, ignore_errors=True)
    return UploadCompleteResponse(file_id=upload_id, filename=meta["filename"], size=actual)
