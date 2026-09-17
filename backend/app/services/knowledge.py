import hashlib
import html
import http.cookiejar
import ipaddress
import json
import math
import re
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from io import BytesIO
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

from ..config import settings
from . import feishu


class KnowledgeError(RuntimeError):
    pass


KNOWLEDGE_DIR = settings.data_dir / "knowledge"
DOCS_DIR = KNOWLEDGE_DIR / "documents"
MAX_DOC_CHARS = 1_500_000
MAX_URL_BYTES = 5 * 1024 * 1024

ALNUM_RE = re.compile(r"[a-zA-Z0-9_]{2,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def ensure_dirs() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def _document_path(doc_id: str) -> Path:
    return DOCS_DIR / f"{doc_id}.json"


def _normalise_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_text(path: Path) -> str:
    return _normalise_text(path.read_text(encoding="utf-8-sig", errors="replace")[:MAX_DOC_CHARS])


def _html_to_text(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return _normalise_text(soup.get_text("\n"))[:MAX_DOC_CHARS]


def _read_pdf(path: Path) -> str:
    reader = PdfReader(str(path), strict=False)
    pages = [(page.extract_text() or "") for page in reader.pages]
    return _normalise_text("\n\n".join(pages))[:MAX_DOC_CHARS]


def _read_docx(path: Path) -> str:
    from docx import Document

    doc = Document(BytesIO(path.read_bytes()))
    parts: list[str] = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return _normalise_text("\n".join(parts))[:MAX_DOC_CHARS]


def _read_pptx(path: Path) -> str:
    from pptx import Presentation

    prs = Presentation(BytesIO(path.read_bytes()))
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if hasattr(shape, "text"):
                parts.append(shape.text)
    return _normalise_text("\n".join(parts))[:MAX_DOC_CHARS]


def _read_xlsx(path: Path) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(path.read_bytes()), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in workbook.worksheets:
        parts.append(f"## {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            parts.append(" | ".join("" if cell is None else str(cell) for cell in row))
    workbook.close()
    return _normalise_text("\n".join(parts))[:MAX_DOC_CHARS]


PLAIN_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".log",
    ".csv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
}
OOXML_SUFFIXES = {".docx", ".pptx", ".xlsx", ".xlsm", ".docm", ".pptm"}
LEGACY_OFFICE_HINT = {
    ".doc": "Word 97-2003",
    ".xls": "Excel 97-2003",
    ".ppt": "PowerPoint 97-2003",
}


def _sniff_kind(path: Path) -> str:
    """按文件头判断真实格式，上传落盘的文件没有扩展名，不能只看文件名。"""
    try:
        head = path.read_bytes()[:8]
    except OSError as exc:
        raise KnowledgeError(f"无法读取上传的文件：{exc}") from exc
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "ooxml"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "legacy_office"
    return "other"


def extract_text(path: Path, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    kind = _sniff_kind(path)

    if kind == "legacy_office":
        label = LEGACY_OFFICE_HINT.get(suffix, "Office 97-2003")
        raise KnowledgeError(
            f"这是 {label} 旧版二进制格式，暂不支持解析。"
            "请用 Office/WPS 打开后「另存为」新版格式（.docx / .xlsx / .pptx）再上传。"
        )

    if suffix in PLAIN_SUFFIXES:
        return _read_text(path)
    if suffix in {".html", ".htm"}:
        return _html_to_text(path.read_text(encoding="utf-8-sig", errors="replace"))

    if suffix == ".pdf" or (kind == "pdf" and suffix not in OOXML_SUFFIXES):
        return _guarded(_read_pdf, path, "PDF")
    if suffix in {".docx", ".docm"}:
        return _guarded(_read_docx, path, "Word 文档")
    if suffix in {".pptx", ".pptm"}:
        return _guarded(_read_pptx, path, "PPT 文档")
    if suffix in {".xlsx", ".xlsm"}:
        return _guarded(_read_xlsx, path, "Excel 表格")

    if not suffix and kind == "ooxml":
        raise KnowledgeError("无法识别文档类型，请确认文件名带有 .docx / .xlsx / .pptx 扩展名。")
    raise KnowledgeError(f"暂不支持该文档类型：{suffix or '未知'}")


def _guarded(reader, path: Path, label: str) -> str:
    try:
        return reader(path)
    except KnowledgeError:
        raise
    except Exception as exc:
        raise KnowledgeError(
            f"{label}解析失败，文件可能已损坏、被加密或格式与扩展名不符（{type(exc).__name__}: {exc}）。"
        ) from exc


def split_text(text: str, max_chars: int = 900, overlap: int = 120) -> list[str]:
    text = _normalise_text(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    chunks: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            chunks.append(buffer.strip())
            buffer = ""

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            candidate = f"{buffer}\n{paragraph}".strip() if buffer else paragraph
            if len(candidate) <= max_chars:
                buffer = candidate
                continue
            flush()
            buffer = paragraph
            continue

        flush()
        step = max_chars - overlap
        for start in range(0, len(paragraph), step):
            chunks.append(paragraph[start : start + max_chars].strip())
        buffer = ""

    flush()
    return [chunk for chunk in chunks if chunk]


def _tokenise(text: str) -> list[str]:
    text = text.lower()
    tokens = [match.group(0) for match in ALNUM_RE.finditer(text)]
    for run in CJK_RE.findall(text):
        tokens.extend(run)
        if len(run) >= 2:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _document_summary(doc: dict) -> dict:
    return {
        "doc_id": doc["doc_id"],
        "title": doc["title"],
        "source_type": doc["source_type"],
        "source_url": doc["source_url"],
        "chunk_count": len(doc["chunks"]),
        "char_count": doc["char_count"],
        "created_at": doc["created_at"],
    }


def _chunks_digest(chunks: list[str]) -> str:
    return hashlib.sha1("\n\n".join(chunks).encode("utf-8")).hexdigest()


def _find_by_hash(content_hash: str) -> dict | None:
    """在已有文档中找内容哈希相同的一份。

    早期入库的文档没有 content_hash 字段，这里顺手补算并回写，
    下次查询就直接命中字段，不用每次都重算。
    """
    for path in DOCS_DIR.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        digest = doc.get("content_hash")
        if not digest:
            digest = _chunks_digest(doc.get("chunks") or [])
            doc["content_hash"] = digest
            try:
                path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        if digest == content_hash:
            return doc
    return None


def add_text(title: str, text: str, source_type: str = "text", source_url: str = "") -> dict:
    ensure_dirs()
    title = title.strip()
    text = _normalise_text(text)
    if not title:
        title = "未命名知识文档"

    # 按正文内容判重：同一份资料换个文件名再传也能识别出来。
    # 分块之间带有重叠，拼回去不等于原文，所以哈希以分块结果为准，
    # 与旧文档懒补哈希的口径保持一致，也与标题无关。
    chunks = split_text(text)
    content_hash = _chunks_digest(chunks)
    existing = _find_by_hash(content_hash)
    if existing is not None:
        raise KnowledgeError(
            f"知识库已存在内容相同的文档《{existing['title']}》，无需重复上传。"
        )

    doc_id = uuid.uuid4().hex
    doc = {
        "doc_id": doc_id,
        "title": title,
        "source_type": source_type,
        "source_url": source_url,
        "content_hash": content_hash,
        "chunks": chunks,
        "char_count": len(text),
        "created_at": int(__import__("time").time()),
    }
    _document_path(doc_id).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return _document_summary(doc)


def list_documents() -> list[dict]:
    ensure_dirs()
    docs: list[dict] = []
    for path in DOCS_DIR.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            docs.append(_document_summary(doc))
        except (json.JSONDecodeError, KeyError):
            continue
    docs.sort(key=lambda item: item["created_at"], reverse=True)
    return docs


def delete_document(doc_id: str) -> None:
    path = _document_path(doc_id)
    if not path.exists():
        raise KnowledgeError("知识文档不存在或已删除")
    path.unlink()


def load_document(doc_id: str) -> dict | None:
    """读取单份文档全文，供题库抽取等场景使用。"""
    path = _document_path(doc_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_chunks() -> list[dict]:
    ensure_dirs()
    chunks: list[dict] = []
    summary_by_id = {}
    for doc in list_documents():
        try:
            summary_by_id[doc["doc_id"]] = doc
            full = json.loads(_document_path(doc["doc_id"]).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            continue
        for index, text in enumerate(full.get("chunks") or []):
            chunks.append(
                {
                    "id": f"{doc['doc_id']}-{index}",
                    "doc_id": doc["doc_id"],
                    "doc_title": doc["title"],
                    "source_type": doc["source_type"],
                    "source_url": doc["source_url"],
                    "text": text,
                }
            )
    return chunks, summary_by_id


def search(query: str, limit: int = 6) -> list[dict]:
    chunks, summary_by_id = _load_chunks()
    if not chunks:
        return []
    query_tokens = _tokenise(query[:3000])
    if not query_tokens:
        return []

    doc_freq = Counter()
    chunk_tokens: list[list[str]] = []
    for chunk in chunks:
        tokens = _tokenise(chunk["text"][:3000])
        chunk_tokens.append(tokens)
        doc_freq.update(set(tokens))

    total = len(chunks)
    avg_length = sum(len(tokens) for tokens in chunk_tokens) / total
    query_counts = Counter(query_tokens)
    scores: list[tuple[float, int]] = []
    k1 = 1.5
    b = 0.75

    for index, tokens in enumerate(chunk_tokens):
        term_freq = Counter(tokens)
        length = len(tokens) or 1
        score = 0.0
        for term, query_tf in query_counts.items():
            tf = term_freq.get(term, 0)
            if tf == 0:
                continue
            df = doc_freq.get(term, 0) or 1
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * length / avg_length))
        if score > 0:
            scores.append((score, index))

    scores.sort(key=lambda item: item[0], reverse=True)
    results: list[dict] = []
    for score, index in scores[:limit]:
        chunk = chunks[index]
        doc_id = chunk["doc_id"]
        summary = summary_by_id.get(doc_id, {})
        results.append(
            {
                "chunk_id": chunk["id"],
                "doc_id": doc_id,
                "title": chunk["doc_title"],
                "source_type": chunk["source_type"],
                "source_url": chunk["source_url"],
                "text": chunk["text"],
                "score": round(score, 3),
                "chunk_count": summary.get("chunk_count", 0),
            }
        )
    return results


FEISHU_HOST_SUFFIXES = (".feishu.cn", ".larksuite.com", ".larkoffice.com")
FEISHU_BLOCK_PREFIX = {
    "heading1": "# ",
    "heading2": "## ",
    "heading3": "### ",
    "heading4": "#### ",
    "heading5": "##### ",
    "heading6": "###### ",
    "bullet": "- ",
    "ordered": "- ",
    "todo": "- [ ] ",
    "quote": "> ",
    "code": "",
}


def _is_feishu_host(host: str) -> bool:
    host = host.lower()
    return host.endswith(FEISHU_HOST_SUFFIXES) or host in {"feishu.cn", "larksuite.com"}


def _extract_json_object(raw: str, key: str) -> dict | None:
    """从页面脚本里按花括号配对截出一个 JSON 对象。"""
    marker = raw.find(key)
    if marker < 0:
        return None
    start = raw.find("{", marker + len(key))
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _feishu_block_text(block: dict) -> str:
    payload = block.get("data", {}).get("text")
    if not isinstance(payload, dict):
        return ""
    initial = payload.get("initialAttributedTexts", {})
    if not isinstance(initial, dict):
        return ""
    fragments = initial.get("text")
    if isinstance(fragments, str):
        return fragments
    if not isinstance(fragments, dict):
        return ""
    ordered_keys = sorted(fragments, key=lambda item: int(item) if item.isdigit() else 0)
    return "".join(str(fragments[key] or "") for key in ordered_keys)


def _feishu_doc_from_html(raw: str) -> tuple[str, str] | None:
    """飞书文档正文以 block_map 形式挂在页面脚本里，DOM 只渲染首屏。"""
    block_map = _extract_json_object(raw, '"block_map":')
    if not isinstance(block_map, dict) or not block_map:
        return None

    root_id = ""
    for block_id, block in block_map.items():
        if isinstance(block, dict) and block.get("data", {}).get("type") == "page":
            root_id = block_id
            break
    if not root_id:
        return None

    lines: list[str] = []
    visited: set[str] = set()

    def collect_text(block_id: str) -> str:
        """把一个块及其子块的文字压平成一行，用于表格单元格。"""
        block = block_map.get(block_id)
        if not isinstance(block, dict):
            return ""
        parts = [_feishu_block_text(block).strip()]
        for child_id in block.get("data", {}).get("children") or []:
            if isinstance(child_id, str):
                parts.append(collect_text(child_id))
        return " ".join(part for part in parts if part).strip()

    def render_table(data: dict) -> None:
        """表格的单元格挂在 cell_set 上，不在 children 里。"""
        rows_id = [item for item in data.get("rows_id") or [] if isinstance(item, str)]
        columns_id = [item for item in data.get("columns_id") or [] if isinstance(item, str)]
        cell_set = data.get("cell_set")
        if not rows_id or not columns_id or not isinstance(cell_set, dict):
            return
        for row_index, row_id in enumerate(rows_id):
            cells: list[str] = []
            for column_id in columns_id:
                cell = cell_set.get(f"{row_id}{column_id}")
                block_id = cell.get("block_id") if isinstance(cell, dict) else None
                if isinstance(block_id, str):
                    visited.add(block_id)
                    for child_id in block_map.get(block_id, {}).get("data", {}).get("children") or []:
                        if isinstance(child_id, str):
                            visited.add(child_id)
                    cells.append(collect_text(block_id).replace("|", "/"))
                else:
                    cells.append("")
            lines.append("| " + " | ".join(cells) + " |")
            if row_index == 0:
                lines.append("| " + " | ".join(["---"] * len(columns_id)) + " |")

    def walk(block_id: str) -> None:
        if block_id in visited:
            return
        visited.add(block_id)
        block = block_map.get(block_id)
        if not isinstance(block, dict):
            return
        data = block.get("data", {})
        block_type = data.get("type", "")
        content = _feishu_block_text(block).strip()
        if block_type == "divider":
            lines.append("---")
        elif content:
            lines.append(FEISHU_BLOCK_PREFIX.get(block_type, "") + content)
        if block_type == "table":
            render_table(data)
            return
        for child_id in data.get("children") or []:
            if isinstance(child_id, str):
                walk(child_id)

    walk(root_id)

    # 飞书首屏只下发约 239 个块，长文档的其余部分要登录后滚动加载。
    # 这里统计缺口，供调用方明确提示用户，避免把残缺正文当成完整文档入库。
    declared = [
        item
        for item in block_map[root_id].get("data", {}).get("children") or []
        if isinstance(item, str)
    ]
    delivered = [item for item in declared if item in block_map]
    if not lines:
        return None

    title = _feishu_block_text(block_map[root_id]).strip()
    body = _normalise_text("\n".join(lines))[:MAX_DOC_CHARS]
    if not title and lines:
        title = lines[0].lstrip("# ").strip()

    if declared and len(delivered) < len(declared):
        raise KnowledgeError(
            f"该飞书文档过长，匿名访问只能拿到前 {len(delivered)} / {len(declared)} 段正文"
            f"（约 {100 * len(delivered) // len(declared)}%），入库会造成内容残缺。"
            "请在飞书里用「下载为 Word/PDF」导出后走本地文件导入，或复制全文用粘贴正文导入。"
        )
    return title, body


def fetch_public_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise KnowledgeError("为保证数据安全，链接导入仅支持 https 地址。")
    host = parsed.hostname or ""
    if not host:
        raise KnowledgeError("链接格式不正确。")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise KnowledgeError("不能导入本地或局域网地址。")
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise KnowledgeError("不能导入本地或内网地址。")
    except ValueError:
        pass

    is_feishu = _is_feishu_host(host)
    if is_feishu and feishu.is_feishu_doc_url(url):
        # 飞书长文档按 cursor 分页下发，静态 HTML 只含首屏，必须走分页抓取。
        try:
            document = feishu.fetch_document(url)
        except feishu.FeishuError as exc:
            raise KnowledgeError(str(exc)) from exc
        if document.text.strip():
            if not document.complete:
                raise KnowledgeError(
                    f"飞书文档抓取不完整（{document.partial_reason}），已放弃入库以免内容残缺。"
                    "请稍后重试，或改用导出文件 / 粘贴正文导入。"
                )
            return document.title or parsed.netloc, document.text

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    # 飞书偶发把请求绕进登录跳转再放行，重试一次即可拿到正文。
    last_error: Exception | None = None
    response = None
    for attempt in range(3):
        try:
            with opener.open(request, timeout=30) as active:
                content_type = active.headers.get_content_type()
                data = active.read(MAX_URL_BYTES + 1)
                response = active
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5)
    if last_error is not None or response is None:
        raise KnowledgeError(
            f"无法获取文档：{last_error}。若文档需要登录或未开启「互联网上获得链接的人可阅读」，"
            "请在飞书里调整分享权限，或改用粘贴正文导入。"
        ) from last_error
    if len(data) > MAX_URL_BYTES:
        raise KnowledgeError("页面内容过大，无法作为单个知识文档导入。")

    if content_type == "application/pdf":
        try:
            return _pdf_bytes_to_text(data)
        except Exception as exc:
            raise KnowledgeError(f"在线 PDF 解析失败：{type(exc).__name__}: {exc}") from exc

    charset = "utf-8"
    if "charset=" in str(response.headers.get("Content-Type", "")):
        charset = response.headers.get_content_charset() or charset
    raw = data.decode(charset, errors="replace")
    title = ""
    if "html" in content_type or "xml" in content_type:
        if is_feishu:
            parsed_doc = _feishu_doc_from_html(raw)
            if parsed_doc and parsed_doc[1].strip():
                return parsed_doc[0] or parsed.netloc, parsed_doc[1]
        soup = BeautifulSoup(raw, "html.parser")
        if soup.title and soup.title.string:
            title = _normalise_text(soup.title.string)
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
            tag.decompose()
        text = _normalise_text(soup.get_text("\n"))
        return title or parsed.netloc, text
    text = _normalise_text(raw)
    return parsed.netloc, text


def _pdf_bytes_to_text(data: bytes) -> tuple[str, str]:
    from io import BytesIO

    reader = PdfReader(BytesIO(data), strict=False)
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = _normalise_text("\n\n".join(pages))[:MAX_DOC_CHARS]
    title = ""
    metadata = reader.metadata
    if metadata and getattr(metadata, "title", None):
        title = _normalise_text(str(metadata.title))
    return title, text
