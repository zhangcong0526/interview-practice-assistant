import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ..schemas import KnowledgeFileRequest, KnowledgeSearchQuery, KnowledgeTextRequest, KnowledgeUrlRequest
from ..services import knowledge as knowledge_service
from .uploads import get_file_record

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post("/files")
async def ingest_file(req: KnowledgeFileRequest):
    record = get_file_record(req.file_id)
    path = Path(record["path"])
    title = Path(record["filename"]).stem
    try:
        text = await asyncio.to_thread(knowledge_service.extract_text, path, record["filename"])
    except knowledge_service.KnowledgeError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"文档解析失败：{type(exc).__name__}: {exc}")
    if not text.strip():
        raise HTTPException(
            422, "未能从文档中提取到文字。若是扫描件或图片型 PDF，请先做 OCR 或改用粘贴正文导入。"
        )
    try:
        document = await asyncio.to_thread(knowledge_service.add_text, title, text, "local_file")
    except knowledge_service.KnowledgeError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"知识入库失败：{type(exc).__name__}: {exc}")
    finally:
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))
    return document


@router.post("/text")
async def ingest_text(req: KnowledgeTextRequest):
    if not req.content.strip():
        raise HTTPException(422, "知识正文不能为空。")
    try:
        return await asyncio.to_thread(
            knowledge_service.add_text,
            req.title,
            req.content,
            req.source_type,
            req.source_url,
        )
    except knowledge_service.KnowledgeError as exc:
        raise HTTPException(422, str(exc))


@router.post("/url")
async def ingest_url(req: KnowledgeUrlRequest):
    title = ""
    text = ""
    try:
        title, text = await asyncio.to_thread(knowledge_service.fetch_public_url, req.url)
    except knowledge_service.KnowledgeError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"链接导入失败：{type(exc).__name__}: {exc}")
    if not text.strip():
        raise HTTPException(422, "未能从链接中提取正文。若文档需要登录或动态渲染，请改用粘贴正文导入。")
    title = req.title.strip() or title or "在线知识文档"
    source_type = req.source_type or "link"
    try:
        return await asyncio.to_thread(
            knowledge_service.add_text, title, text, source_type, req.url
        )
    except knowledge_service.KnowledgeError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"知识入库失败：{type(exc).__name__}: {exc}")


@router.get("/documents")
async def list_documents():
    return knowledge_service.list_documents()


@router.get("/search")
async def search_knowledge(
    q: str = Query(min_length=1, max_length=5000),
    limit: int = Query(default=6, ge=1, le=20),
):
    return knowledge_service.search(q, limit)


@router.delete("/documents/{doc_id}")
async def remove_document(doc_id: str):
    try:
        return await asyncio.to_thread(knowledge_service.delete_document, doc_id)
    except knowledge_service.KnowledgeError as exc:
        raise HTTPException(404, str(exc))
