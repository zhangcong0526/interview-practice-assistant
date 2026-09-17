import shutil
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import analyze, expression, interview, jobs, knowledge, quiz, resume, tts, uploads
from .routers.jobs import RESULTS_DIR, WORK_DIR
from .routers.uploads import UPLOADS_DIR


def _cleanup_stale() -> None:
    """启动时清理 24 小时前的残留上传会话和临时工作目录。"""
    cutoff = time.time() - 24 * 3600
    for base in (UPLOADS_DIR, WORK_DIR):
        if not base.exists():
            continue
        for entry in base.iterdir():
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_stale()
    yield


app = FastAPI(title="面试练习助手", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(uploads.router)
app.include_router(jobs.router)
app.include_router(analyze.router)
app.include_router(knowledge.router)
app.include_router(interview.router)
app.include_router(resume.router)
app.include_router(quiz.router)
app.include_router(tts.router)
app.include_router(expression.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
