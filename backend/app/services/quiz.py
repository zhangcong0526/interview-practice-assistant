"""在线考题：关键词提炼、组卷、判分、错题本与掌握度追踪。

判分完全在本地完成，不依赖 LLM，保证成绩可复现；LLM 只负责命题和复习指引。
"""

import json
import re
import logging
import shutil
import time
import uuid
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import settings
from .. import prompts
from . import knowledge as knowledge_service
from . import llm as llm_service
from . import review as review_service


class QuizError(RuntimeError):
    pass


QUIZ_DIR = settings.data_dir / "quiz"
PAPERS_DIR = QUIZ_DIR / "papers"
ATTEMPTS_DIR = QUIZ_DIR / "attempts"
MISTAKES_FILE = QUIZ_DIR / "mistakes.json"
MASTERY_FILE = QUIZ_DIR / "mastery.json"
MASTERY_META_FILE = QUIZ_DIR / "mastery_meta.json"
TOPICS_FILE = QUIZ_DIR / "topics.json"

VALID_TYPES = ("single", "multiple", "judge")
TYPE_LABELS = {"single": "单选题", "multiple": "多选题", "judge": "判断题"}
MAX_MATERIAL_CHARS = 24_000
MAX_KEYWORD_MATERIAL_CHARS = 12_000
MAX_REVIEW_SOURCE_CHARS = 2_400
MAX_REVIEW_REFS_PER_TOPIC = 1
MAX_REVIEW_REF_CHARS = 400
MAX_QUESTIONS = 30
QUIZ_LLM_BATCH_SIZE = 8
QUIZ_FAST_SINGLE_CALL_LIMIT = 8
MIN_FALLBACK_EXPLANATION_CHARS = 24
MIN_STRICT_EXPLANATION_CHARS = 60

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
quiz_logger = logging.getLogger("interview_assistant.quiz")
if not quiz_logger.handlers:
    _quiz_handler = RotatingFileHandler(
        LOG_DIR / "quiz.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _quiz_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    quiz_logger.addHandler(_quiz_handler)
    quiz_logger.setLevel(logging.INFO)


def _minimax_provider() -> bool:
    return str(getattr(settings, "llm_provider", "")) == "minimax"


def _quiz_max_tokens() -> int:
    if _minimax_provider():
        try:
            return max(2000, int(getattr(settings, "minimax_max_tokens", 16000)))
        except (TypeError, ValueError):
            return 16000
    return 8000


def _quiz_batch_size() -> int:
    if _minimax_provider():
        try:
            return max(1, int(getattr(settings, "minimax_batch_size", 6)))
        except (TypeError, ValueError):
            return 6
    return QUIZ_LLM_BATCH_SIZE


def _resolve_review_channel() -> tuple[str | None, str | None]:
    """复习模型不能把 A 厂商的 Model ID 传给 B 厂商的兼容接口。"""
    if _minimax_provider():
        if settings.deepseek_api_key:
            return "deepseek", "deepseek-chat"
        if settings.ark_api_key and settings.ark_model:
            return "ark", settings.ark_model
        return "minimax", None
    return None, settings.review_model or None
REVIEW_BATCH_SIZE = 2
REVIEW_MAX_CONCURRENCY = 3
REVIEW_LLM_ITEM_LIMIT = 8
REVIEW_TIMEOUT_SECONDS = 30.0
REVIEW_FALLBACK_BATCH_SIZE = 1
REVIEW_FALLBACK_CONCURRENCY = 3
REVIEW_FALLBACK_TIMEOUT_SECONDS = 15.0
MASTERY_LEVEL_ORDER = {"beginner": 0, "developing": 1, "proficient": 2, "mastered": 3}
# 连续两次答对即视为该知识点已回稳，可以移出错题本。
MISTAKE_CLEAR_STREAK = 2
MASTERY_VERSION = 3
SIMILAR_STEM_THRESHOLD = 0.72
GENERIC_TOPIC_ALIASES = {
    "资源",
    "工具",
    "提示",
    "状态",
    "路由",
    "检索",
    "测试",
    "面试",
    "练习",
    "方法",
    "流程",
    "问题",
}


def ensure_dirs() -> None:
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    ATTEMPTS_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _write_json(path: Path, data) -> None:
    ensure_dirs()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalise_topic_key(topic: str) -> str:
    """用于匹配 LLM 生成知识点与题库关键词的别名、空格和大小写差异。"""
    text = unicodedata.normalize("NFKC", str(topic or "")).strip().casefold()
    return re.sub(r"[\s·•:：,，。；;、/\\|\-—_（）()\[\]【】{}\"'“”‘’!?！？]+", "", text)


def _topic_catalog() -> dict:
    """从关键词缓存中汇总规范名、别名和板块。

    关键词缓存可能按不同资料范围保存多份结果，因此板块采用加权投票，
    避免某一次 LLM 标引的偶发分类影响全局统计。
    """
    cache = _read_json(TOPICS_FILE, {})
    display: dict[str, str] = {}
    weights: dict[str, int] = {}
    categories: dict[str, dict[str, int]] = {}
    aliases: dict[str, str] = {}
    if not isinstance(cache, dict):
        return {"display": display, "categories": categories, "aliases": aliases}

    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        for item in entry.get("topics") or []:
            if not isinstance(item, dict):
                continue
            keyword = str(item.get("keyword") or "").strip()
            if not keyword:
                continue
            canonical = _normalise_topic_key(keyword)
            if not canonical:
                continue
            try:
                weight = max(1, min(5, int(item.get("weight") or 1)))
            except (TypeError, ValueError):
                weight = 1
            if canonical not in weights or weight > weights[canonical]:
                display[canonical] = keyword
                weights[canonical] = weight
            category = str(item.get("category") or "").strip() or "未分类"
            category_votes = categories.setdefault(canonical, {})
            category_votes[category] = category_votes.get(category, 0) + weight
            for alias in item.get("aliases") or []:
                alias_text = str(alias or "").strip()
                alias_key = _normalise_topic_key(alias_text)
                if not alias_key or alias_key == canonical or alias_key in GENERIC_TOPIC_ALIASES:
                    continue
                # 两字以下泛称容易误合并，只有明确英文缩写允许映射。
                if len(alias_key) < 3 and not re.search(r"[a-z0-9]", alias_key):
                    continue
                existing = aliases.get(alias_key)
                if existing and existing != canonical:
                    aliases.pop(alias_key, None)
                elif not existing:
                    aliases[alias_key] = canonical

    resolved_categories = {
        canonical: max(votes.items(), key=lambda item: item[1])[0]
        for canonical, votes in categories.items()
    }
    return {
        "display": display,
        "categories": resolved_categories,
        "aliases": aliases,
        "weights": weights,
    }


def _canonical_topic(topic: str, catalog: dict | None = None) -> str:
    raw = unicodedata.normalize("NFKC", str(topic or "")).strip()
    if not raw:
        return "未分类"
    catalog = catalog or _topic_catalog()
    key = _normalise_topic_key(raw)
    canonical = catalog.get("aliases", {}).get(key) or key
    return catalog.get("display", {}).get(canonical, raw)


def _topic_module(topic: str, catalog: dict) -> str:
    key = _normalise_topic_key(topic)
    canonical = catalog.get("aliases", {}).get(key, key)
    raw_category = catalog.get("categories", {}).get(canonical, "")
    category_key = _normalise_topic_key(raw_category)
    source = f"{category_key} {key}"

    ai_terms = (
        "ai", "llm", "大模型", "rag", "agent", "智能体", "prompt", "embedding",
        "向量", "transformer", "token", "hallucination", "幻觉", "mcp",
        "functioncalling", "微调",
    )
    hardware_terms = (
        "can", "485", "bms", "电机", "驱动器", "雷达", "imu", "编码器", "执行器",
        "控制器", "烧录", "mcu", "老化", "整机", "急停", "回充", "导航", "定位",
        "硬件", "电池", "机器人", "ros", "传感器",
    )
    communication_terms = (
        "通信", "协议", "mqtt", "udp", "tcp", "iot", "物联网", "can", "485",
    )
    management_terms = (
        "okr", "kpi", "人才", "离职", "沟通", "岗位匹配", "梯队", "冲突",
        "职业", "团队", "表达",
    )
    software_terms = (
        "测试流程", "测试管理", "专项", "数据库", "接口", "自动化", "性能", "web", "app",
        "小程序", "linux", "python", "软件测试",
    )
    if any(term in key for term in ai_terms):
        return "AI大模型与Agent测试"
    if any(term in source for term in ai_terms):
        return "AI大模型与Agent测试"
    if any(term in source for term in hardware_terms):
        return "机器人与智能硬件测试"
    if any(term in source for term in communication_terms):
        return "通信协议与物联网"
    if any(term in source for term in management_terms):
        return "职业素养与团队管理"
    if any(term in source for term in software_terms):
        return "传统软件测试"
    return "综合测试能力"


def _empty_type_stat() -> dict:
    return {"total": 0, "correct": 0}


def _empty_topic_stat(now: int = 0) -> dict:
    return {
        "total": 0,
        "correct": 0,
        "streak": 0,
        "updated_at": now,
        "by_type": {qtype: _empty_type_stat() for qtype in VALID_TYPES},
    }


def _normalise_topic_stat(stat, now: int = 0) -> dict:
    if not isinstance(stat, dict):
        return _empty_topic_stat(now)
    normalised = _empty_topic_stat(now)
    for key in ("total", "correct", "streak"):
        try:
            normalised[key] = max(0, int(stat.get(key, normalised[key])))
        except (TypeError, ValueError):
            pass
    if stat.get("updated_at"):
        normalised["updated_at"] = stat.get("updated_at")
    by_type = stat.get("by_type") if isinstance(stat.get("by_type"), dict) else {}
    for qtype in VALID_TYPES:
        current = by_type.get(qtype) if isinstance(by_type.get(qtype), dict) else {}
        try:
            normalised["by_type"][qtype]["total"] = max(0, int(current.get("total", 0)))
        except (TypeError, ValueError):
            pass
        try:
            normalised["by_type"][qtype]["correct"] = max(0, int(current.get("correct", 0)))
        except (TypeError, ValueError):
            pass
    return normalised


def _ensure_mastery_schema() -> dict:
    """升级掌握度结构，并按规范知识点名归并历史别名。"""
    meta = _read_json(MASTERY_META_FILE, {})
    mastery = _read_json(MASTERY_FILE, {})
    catalog = _topic_catalog()
    if isinstance(meta, dict) and (meta.get("version") or 0) >= MASTERY_VERSION and isinstance(mastery, dict):
        normalised = {
            topic: _normalise_topic_stat(stat)
            for topic, stat in mastery.items()
            if isinstance(stat, dict)
        }
        mapped = [_canonical_topic(topic, catalog) for topic in normalised]
        needs_canonicalisation = any(
            canonical != topic for topic, canonical in zip(normalised, mapped)
        ) or len(set(mapped)) != len(mapped)
        if not needs_canonicalisation:
            return normalised

    old_version = int(meta.get("version") or 1) if isinstance(meta, dict) else 1
    if MASTERY_FILE.exists():
        backup_name = f"mastery.v{old_version}.backup.json"
        backup = QUIZ_DIR / backup_name
        if not backup.exists():
            shutil.copyfile(MASTERY_FILE, backup)

    # v3 直接从历史答卷重建：这样总分、连对和题型统计都按时间顺序计算，
    # 同时把 LLM 曾经生成的别名、空格/大小写差异归并到规范知识点。
    rebuilt: dict[str, dict] = {}
    if ATTEMPTS_DIR.exists():
        paths = sorted(ATTEMPTS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
        for path in paths:
            attempt = _read_json(path, None)
            if not attempt:
                continue
            for question in attempt.get("questions") or []:
                if not isinstance(question, dict):
                    continue
                topic = _canonical_topic(str(question.get("topic") or "未分类"), catalog)
                qtype = str(question.get("type") or "").strip()
                if qtype not in VALID_TYPES:
                    continue
                stat = rebuilt.setdefault(topic, _empty_topic_stat(int(attempt.get("created_at") or 0)))
                stat["total"] += 1
                stat["by_type"][qtype]["total"] += 1
                if question.get("is_correct"):
                    stat["correct"] += 1
                    stat["streak"] += 1
                    stat["by_type"][qtype]["correct"] += 1
                else:
                    stat["streak"] = 0
                stat["updated_at"] = int(attempt.get("created_at") or stat.get("updated_at") or 0)

    normalised_mastery: dict[str, dict] = {}
    if isinstance(mastery, dict):
        for topic, stat in mastery.items():
            canonical = _canonical_topic(str(topic), catalog)
            if canonical in rebuilt:
                continue
            normalised_mastery[canonical] = _normalise_topic_stat(stat)
    for topic, stat in rebuilt.items():
        normalised_mastery[topic] = _normalise_topic_stat(stat)

    _write_json(MASTERY_FILE, normalised_mastery)
    _write_json(
        MASTERY_META_FILE,
        {"version": MASTERY_VERSION, "migrated_at": int(time.time())},
    )
    return normalised_mastery


# --------------------------------------------------------------------------
# 资料检索
# --------------------------------------------------------------------------


def _collect_material(
    keywords: list[str], doc_ids: list[str], per_keyword: int = 4
) -> tuple[str, list[str], list[dict], list[dict]]:
    """按关键词检索知识库片段，拼成命题资料。

    没有关键词时退化为使用指定文档（或全部文档）的开头内容。
    同时返回引用文档元数据，供错题本回溯原始出处。
    """
    used_titles: list[str] = []
    used_docs: list[dict] = []
    used_chunks: list[dict] = []
    doc_by_id: dict[str, dict] = {}
    blocks: list[str] = []
    seen_chunks: set[str] = set()
    budget = MAX_MATERIAL_CHARS

    def remember_doc(doc_id: str, title: str) -> None:
        if not doc_id:
            return
        if doc_id not in doc_by_id:
            doc_by_id[doc_id] = {"doc_id": doc_id, "title": title}
            used_docs.append(doc_by_id[doc_id])
        if title and title not in used_titles:
            used_titles.append(title)

    def remember_chunk(doc_id: str, title: str, chunk_id: str) -> None:
        if not doc_id or not chunk_id:
            return
        if not any(
            chunk.get("doc_id") == doc_id and chunk.get("chunk_id") == chunk_id
            for chunk in used_chunks
        ):
            used_chunks.append({"doc_id": doc_id, "title": title, "chunk_id": chunk_id})

    def push(title: str, text: str, chunk_id: str, doc_id: str = "") -> None:
        nonlocal budget
        if chunk_id in seen_chunks or budget <= 0:
            return
        seen_chunks.add(chunk_id)
        snippet = text[:budget]
        blocks.append(f"[资料来源：{title}]\n{snippet}")
        budget -= len(snippet)
        remember_doc(doc_id, title)
        remember_chunk(doc_id, title, chunk_id)

    doc_filter = set(doc_ids or [])

    for keyword in keywords:
        for hit in knowledge_service.search(keyword, per_keyword):
            if doc_filter and hit["doc_id"] not in doc_filter:
                continue
            push(hit["title"], hit["text"], hit["chunk_id"], hit["doc_id"])

    if not blocks:
        for doc in knowledge_service.list_documents():
            if doc_filter and doc["doc_id"] not in doc_filter:
                continue
            full = _load_document_text(doc["doc_id"])
            if full:
                push(doc["title"], full, doc["doc_id"], doc["doc_id"])

    return "\n\n".join(blocks), used_titles, used_docs, used_chunks


def _load_document_text(doc_id: str) -> str:
    path = knowledge_service.DOCS_DIR / f"{doc_id}.json"
    doc = _read_json(path, None)
    if not doc:
        return ""
    return "\n\n".join(doc.get("chunks") or [])


def _ranked_doc_chunks(doc: dict, topic: str, max_chunks: int = 2) -> list[tuple[int, str]]:
    """从来源文档中挑与知识点最相关的片段；无命中时按文档顺序兜底。"""
    terms = [t for t in re.split(r"[\s/、，,;；()（）·]+", topic or "") if len(t) >= 2]
    scored: list[tuple[int, int, str]] = []
    for index, text in enumerate(doc.get("chunks") or []):
        text = str(text or "")
        if not text:
            continue
        score = sum(text.count(term) for term in terms)
        scored.append((-score, index, text))
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return [(index, text) for _, index, text in scored[:max_chunks]]


def _title_key(title: str) -> str:
    return _normalise_topic_key(title)


def _collect_review_sources(
    graded: list[dict],
    topic_stats: dict[str, dict] | None = None,
    paper_docs: dict | None = None,
) -> dict[str, list[dict]]:
    """为复习指引检索原文片段，避免建议脱离用户上传的知识库。"""
    topic_stats = topic_stats or {}
    wanted: dict[str, list[str]] = {}
    lineage_docs: dict[str, list[dict]] = {}
    lineage_chunks: dict[str, list[str]] = {}
    docs_by_title = _knowledge_docs_by_title()
    fallback_paper_docs = _mistake_paper_docs(paper_docs)

    def add_chunks(topic: str, chunk_ids) -> None:
        known = lineage_chunks.setdefault(topic, [])
        for chunk_id in chunk_ids or []:
            value = str(chunk_id or "").strip()
            if value and value not in known:
                known.append(value)

    def add_chunk_lineage(topic: str, chunk_ids, title: str = "") -> None:
        for chunk_id in chunk_ids or []:
            doc_id = str(chunk_id or "").strip().rsplit("-", 1)[0]
            if doc_id:
                add_lineage(topic, [{"doc_id": doc_id, "title": title}])

    def add_lineage(topic: str, docs) -> None:
        known = lineage_docs.setdefault(topic, [])
        known_ids = {doc["doc_id"] for doc in known}
        for doc in docs or []:
            doc_id = str((doc or {}).get("doc_id") or "").strip()
            title = str((doc or {}).get("title") or "").strip()
            matched = docs_by_title.get(_title_key(title)) if title else None
            if not doc_id and matched:
                doc_id = str(matched.get("doc_id") or "")
            if not doc_id or doc_id in known_ids:
                continue
            known_ids.add(doc_id)
            known.append(
                {
                    "doc_id": doc_id,
                    "title": title or str((matched or {}).get("title") or ""),
                }
            )

    for item in graded:
        topic = str(item.get("topic") or "未分类").strip() or "未分类"
        history = topic_stats.get(topic) or {}
        try:
            accuracy = float(history.get("correct", 0)) / max(1, int(history.get("total", 0)))
        except (TypeError, ValueError):
            accuracy = 1.0
        if item.get("is_correct") and accuracy >= 0.8:
            continue
        source_title = str(item.get("source_title") or "").strip()
        titles = wanted.setdefault(topic, [])
        if source_title and source_title not in titles:
            titles.append(source_title)
        add_lineage(
            topic,
            [{"doc_id": item.get("source_doc_id") or "", "title": source_title}],
        )
        add_chunks(topic, item.get("source_chunk_ids"))
        if not item.get("source_doc_id") and not item.get("source_chunk_ids"):
            add_lineage(topic, fallback_paper_docs)
        elif not item.get("source_doc_id"):
            add_chunk_lineage(topic, item.get("source_chunk_ids"), source_title)

    # 历史错题可能来自同一知识点的其他文档。这里只合并来源标题，
    # 不直接拿历史题干，避免复习步骤被旧题目带偏。
    for mistake in list_mistakes(limit=10_000):
        topic = str(mistake.get("topic") or "").strip()
        if topic not in wanted:
            continue
        source_title = str(mistake.get("source_title") or "").strip()
        titles = wanted[topic]
        if source_title and source_title not in titles:
            titles.append(source_title)
        add_lineage(topic, mistake.get("source_docs"))
        add_chunks(topic, mistake.get("source_chunk_ids"))

    sources: dict[str, list[dict]] = {}
    for topic, source_titles in wanted.items():
        search_queries = [topic]
        for source_title in source_titles:
            search_queries.append(f"{source_title} {topic}")

        hits: list[dict] = []
        seen_chunks: set[str] = set()
        lineage_refs: list[dict] = []

        # 老错题补齐过文档线索后，直接读原文档片段，避免搜索匹配不到出处。
        for doc in lineage_docs.get(topic, []):
            loaded = knowledge_service.load_document(doc["doc_id"])
            if not loaded:
                continue
            doc_title = str(loaded.get("title") or doc.get("title") or "")
            source_url = str(loaded.get("source_url") or "")
            doc_chunks = loaded.get("chunks") or []
            preferred_chunks: list[tuple[int, str]] = []
            for chunk_id in lineage_chunks.get(topic, []):
                if not chunk_id.startswith(f"{doc['doc_id']}-"):
                    continue
                suffix = chunk_id[len(doc["doc_id"]) + 1 :]
                if not suffix.isdigit():
                    continue
                index = int(suffix)
                if 0 <= index < len(doc_chunks):
                    preferred_chunks.append((index, str(doc_chunks[index] or "")))

            if preferred_chunks:
                doc_chunk_results = preferred_chunks
            else:
                doc_chunk_results = _ranked_doc_chunks(loaded, topic)

            for index, text in doc_chunk_results:
                chunk_id = f"{doc['doc_id']}-{index}"
                if chunk_id in seen_chunks:
                    continue
                seen_chunks.add(chunk_id)
                lineage_refs.append(
                    {
                        "chunk_id": chunk_id,
                        "doc_id": doc["doc_id"],
                        "title": doc_title,
                        "source_url": source_url,
                        "text": text,
                    }
                )

        # 已有可信片段来源时不做全库搜索，避免综合材料把复习范围扩散。
        # 来源文档被删除或片段无法命中时，再降级为原有全库检索。
        if lineage_refs:
            hits.extend(lineage_refs)
        else:
            for query in search_queries:
                try:
                    results = knowledge_service.search(query, limit=6)
                except Exception:
                    results = []
                for hit in results:
                    chunk_id = str(hit.get("chunk_id") or "")
                    if not chunk_id or chunk_id in seen_chunks:
                        continue
                    seen_chunks.add(chunk_id)
                    hits.append(hit)

        # 已知来源文档的片段排前面，但不再把其他文档过滤掉。
        # 这样同一知识点在多份文档里交叉出现时，复习建议能同时引用。
        title_keys = {_title_key(title) for title in source_titles if title}
        ranked_hits = sorted(
            enumerate(hits),
            key=lambda pair: (
                _title_key(str(pair[1].get("title") or "")) not in title_keys,
                pair[0],
            ),
        )
        hits = [hit for _, hit in ranked_hits]

        selected: list[dict] = []
        used_chars = 0
        selected_ids: set[str] = set()

        def append_ref(hit: dict) -> bool:
            nonlocal used_chars
            chunk_id = str(hit.get("chunk_id") or "")
            if chunk_id in selected_ids:
                return False
            text = str(hit.get("text") or "").strip()
            if not text:
                return False
            text = text[:700]
            if used_chars + len(text) > MAX_REVIEW_SOURCE_CHARS:
                return False
            used_chars += len(text)
            selected_ids.add(chunk_id)
            selected.append(
                {
                    "title": str(hit.get("title") or "知识库文档"),
                    "text": text,
                    "url": str(hit.get("source_url") or "").strip(),
                }
            )
            return True

        # 先每个文档取一个最相关片段，再按相关度补足剩余名额，
        # 避免同一份文档把全部引用占满。
        first_seen_titles: set[str] = set()
        for hit in hits:
            title = str(hit.get("title") or "知识库文档")
            if title in first_seen_titles:
                continue
            if append_ref(hit):
                first_seen_titles.add(title)
            if len(selected) >= 3:
                break

        for hit in hits:
            if len(selected) >= 3:
                break
            append_ref(hit)

        if selected:
            sources[topic] = selected

    return sources


def _format_review_sources(sources: dict[str, list[dict]]) -> str:
    blocks: list[str] = []
    for topic, refs in sources.items():
        lines = [f"【知识点：{topic}】"]
        for index, ref in enumerate(refs[:MAX_REVIEW_REFS_PER_TOPIC], start=1):
            url = str(ref.get("url") or "").strip()
            suffix = f"（链接：{url}）" if url else ""
            lines.append(f"片段{index} · 《{ref.get('title', '')}》{suffix}")
            lines.append(str(ref.get("text") or "")[:MAX_REVIEW_REF_CHARS].strip())
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_EXPLANATION_BAD_PHRASES = (
    "等等，",
    "重新计算",
    "重新审视",
    "需要重新",
    "调整题干",
    "我们改",
    "我们调整",
    "我们采用",
    "换个角度",
    "选项中没有",
    "但为了符合",
)
_EXPLANATION_BAD_REGEX = re.compile("|".join(_EXPLANATION_BAD_PHRASES))


def _clean_explanation(text: str) -> str:
    """展示用清洗：超长解析或包含模型自纠错短语时，截断到首个嫌疑词前。"""
    raw = str(text or "")
    if not raw:
        return raw
    if len(raw) > 800:
        return "解析过长可能包含模型自纠错草稿，建议重做本题验证。"
    match = _EXPLANATION_BAD_REGEX.search(raw)
    if match:
        cleaned = raw[: match.start()].rstrip()
        if cleaned:
            return cleaned
        return "解析包含模型自纠错草稿，建议重做本题验证。"
    return raw

# --------------------------------------------------------------------------
# 关键词
# --------------------------------------------------------------------------


def extract_topics(doc_ids: list[str] | None = None, refresh: bool = False) -> list[dict]:
    """从知识库文档中提炼可勾选的知识点关键词，结果带缓存。"""
    doc_ids = doc_ids or []
    cache = _read_json(TOPICS_FILE, {})
    cache_key = ",".join(sorted(doc_ids)) or "__all__"
    documents = knowledge_service.list_documents()
    if doc_ids:
        documents = [doc for doc in documents if doc["doc_id"] in set(doc_ids)]
    if not documents:
        raise QuizError("知识库还没有文档，请先导入面试题库或学习资料。")

    signature = "|".join(f"{doc['doc_id']}:{doc['char_count']}" for doc in documents)
    cached = cache.get(cache_key)
    if cached and cached.get("signature") == signature and not refresh:
        return cached.get("topics") or []

    # 全库标引不需要读完整正文。均摊预算能避免第一份长文档占满输入，
    # 同时把所有文档的标题和开头片段都交给模型，关键词覆盖更均衡。
    material_parts: list[str] = []
    keyword_budget = MAX_KEYWORD_MATERIAL_CHARS
    per_doc_budget = max(400, keyword_budget // max(1, len(documents)))
    keyword_started_at = time.perf_counter()
    for doc in documents:
        if keyword_budget <= 0:
            break
        text = _load_document_text(doc["doc_id"])[:per_doc_budget]
        if text:
            text = text[:keyword_budget]
            material_parts.append(f"[资料来源：{doc['title']}]\n{text}")
            keyword_budget -= len(text)
    material = "\n\n".join(material_parts)
    if not material.strip():
        raise QuizError("选中的文档没有可用正文。")

    try:
        raw = llm_service.chat_json(
            prompts.KEYWORD_SYSTEM, prompts.build_keyword_user(material)
        )
    except llm_service.LlmError as exc:
        raise QuizError(f"关键词提炼失败：{exc}") from exc

    topics: list[dict] = []
    seen: set[str] = set()
    for item in raw.get("topics") or []:
        if not isinstance(item, dict):
            continue
        keyword = str(item.get("keyword") or "").strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        try:
            weight = int(item.get("weight") or 1)
        except (TypeError, ValueError):
            weight = 1
        topics.append(
            {
                "keyword": keyword,
                "aliases": [
                    str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()
                ],
                "category": str(item.get("category") or "未分类").strip() or "未分类",
                "weight": max(1, min(5, weight)),
            }
        )
    topics.sort(key=lambda item: item["weight"], reverse=True)

    quiz_logger.info(
        "topics extracted docs=%d material_chars=%d elapsed=%.2fs",
        len(documents),
        MAX_KEYWORD_MATERIAL_CHARS - keyword_budget,
        time.perf_counter() - keyword_started_at,
    )

    cache[cache_key] = {"signature": signature, "topics": topics}
    _write_json(TOPICS_FILE, cache)
    return topics


# --------------------------------------------------------------------------
# 组卷
# --------------------------------------------------------------------------


def _stem_features(stem: str) -> set[str]:
    text = unicodedata.normalize("NFKC", stem or "").casefold()
    tokens = re.findall(r"[\u4e00-\u9fff]|[a-z0-9]+", text)
    features: set[str] = set()
    chinese = "".join(token for token in tokens if re.fullmatch(r"[\u4e00-\u9fff]", token))
    if len(chinese) <= 1:
        features.update(chinese)
    else:
        features.update(chinese[index : index + 2] for index in range(len(chinese) - 1))
    features.update(token for token in tokens if re.fullmatch(r"[a-z0-9]+", token))
    return features


def _stem_similarity(left: str, right: str) -> float:
    left_features = _stem_features(left)
    right_features = _stem_features(right)
    if not left_features or not right_features:
        return 0.0
    return len(left_features & right_features) / len(left_features | right_features)


def _dedupe_questions(
    candidates: list[dict],
    recent_stems: list[str],
    accepted: list[dict] | None = None,
    recent_threshold: float = SIMILAR_STEM_THRESHOLD,
) -> list[dict]:
    accepted = accepted or []
    current_stems = [item["stem"] for item in accepted]
    for candidate in candidates:
        if any(
            _stem_similarity(candidate["stem"], old_stem) >= recent_threshold
            for old_stem in recent_stems
        ):
            continue
        if any(
            _stem_similarity(candidate["stem"], old_stem) >= SIMILAR_STEM_THRESHOLD
            for old_stem in current_stems
        ):
            continue
        accepted.append(candidate)
        current_stems.append(candidate["stem"])
    return accepted


def _buffered_counts(counts: dict[str, int], buffer_size: int = 4) -> dict[str, int]:
    candidate_counts = dict(counts)
    remaining = min(buffer_size, MAX_QUESTIONS - sum(counts.values()))
    priority = ("multiple", "judge", "single")
    index = 0
    while remaining > 0 and any(candidate_counts[qtype] > 0 for qtype in VALID_TYPES):
        qtype = priority[index % len(priority)]
        index += 1
        if candidate_counts[qtype] == 0:
            continue
        candidate_counts[qtype] += 1
        remaining -= 1
    return candidate_counts


def _quiz_count_batches(counts: dict[str, int]) -> list[dict[str, int]]:
    remaining = {
        qtype: max(0, int(counts.get(qtype, 0)))
        for qtype in VALID_TYPES
    }
    batches: list[dict[str, int]] = []
    while any(remaining.values()):
        batch = {qtype: 0 for qtype in VALID_TYPES}
        batch_total = 0
        cap = _quiz_batch_size()
        for qtype in VALID_TYPES:
            take = min(remaining[qtype], cap - batch_total)
            batch[qtype] = take
            batch_total += take
            remaining[qtype] -= take
            if batch_total >= cap:
                break
        batches.append(batch)
    return batches


def _request_quiz_raw_once(
    material: str,
    keywords: list[str],
    counts: dict[str, int],
    difficulty: str,
    avoid_stems: list[str],
    weak_topics: list[str],
    coverage_hint: list[str],
) -> dict:
    # Ark 的思考型模型生成完整试卷常超过 120 秒；组卷只依赖结构化 JSON，
    # 这里优先复用已配置的 DeepSeek Chat 作为低延迟命题通道，避免主模型被单独卡住。
    quiz_provider = None
    quiz_model = None
    if settings.llm_provider == "ark" and settings.deepseek_api_key:
        quiz_provider = "deepseek"
        quiz_model = "deepseek-chat"
    elif settings.llm_provider == "minimax":
        if settings.deepseek_api_key:
            quiz_provider = "deepseek"
            quiz_model = "deepseek-chat"
        elif settings.ark_api_key and settings.ark_model:
            quiz_provider = "ark"
            quiz_model = settings.ark_model
    return llm_service.chat_json(
        prompts.QUIZ_SYSTEM,
        prompts.build_quiz_user(
            material,
            keywords,
            counts,
            difficulty,
            avoid_stems,
            weak_topics,
            coverage_hint,
        ),
        provider=quiz_provider,
        model=quiz_model,
        max_tokens=_quiz_max_tokens(),
    )


def _request_quiz_raw_batched(
    material: str,
    keywords: list[str],
    counts: dict[str, int],
    difficulty: str,
    avoid_stems: list[str],
    weak_topics: list[str],
    coverage_hint: list[str],
) -> dict:
    batches = _quiz_count_batches(counts)
    if len(batches) == 1:
        return _request_quiz_raw_once(
            material,
            keywords,
            batches[0],
            difficulty,
            avoid_stems,
            weak_topics,
            coverage_hint,
        )

    parts: list[dict | None] = [None] * len(batches)

    def request_batch(index: int, batch: dict[str, int]) -> tuple[int, dict]:
        return index, _request_quiz_raw_once(
            material,
            keywords,
            batch,
            difficulty,
            avoid_stems,
            weak_topics,
            coverage_hint,
        )

    with ThreadPoolExecutor(max_workers=min(3, len(batches))) as executor:
        futures = [
            executor.submit(request_batch, index, batch)
            for index, batch in enumerate(batches)
        ]
        for future in as_completed(futures):
            index, part = future.result()
            parts[index] = part

    merged: dict = {"title": "", "questions": []}
    for part in parts:
        if part is None:
            continue
        if not merged["title"]:
            merged["title"] = str(part.get("title") or "")
        merged["questions"].extend(part.get("questions") or [])
    return merged


def _request_quiz_raw(
    material: str,
    keywords: list[str],
    counts: dict[str, int],
    difficulty: str,
    avoid_stems: list[str],
    weak_topics: list[str],
    coverage_hint: list[str],
) -> dict:
    total = sum(max(0, int(counts.get(qtype, 0))) for qtype in VALID_TYPES)
    if total <= QUIZ_FAST_SINGLE_CALL_LIMIT:
        try:
            return _request_quiz_raw_once(
                material,
                keywords,
                counts,
                difficulty,
                avoid_stems,
                weak_topics,
                coverage_hint,
            )
        except llm_service.LlmError as exc:
            if "长度上限" not in str(exc):
                raise

    return _request_quiz_raw_batched(
        material,
        keywords,
        counts,
        difficulty,
        avoid_stems,
        weak_topics,
        coverage_hint,
    )


def _has_unreliable_explanation(explanation: str) -> bool:
    """拦截命题模型把思考草稿写进 explanation 的坏题。"""
    text = str(explanation or "")
    if len(text) > 800:
        return True
    patterns = (
        r"等等[，,、]",
        r"(重新计算|重新审视|重新考虑)",
        r"(题干|资料|解析|计算|推理)(可能|存在)?(有误|不完整|不一致)",
        r"(调整|修改|更换)题干",
        r"我们(改为|采用|调整|重新)",
        r"换(一个|个)角度",
        r"选项中(没有|不包含)",
        r"(但|但为了)(为了)?(符合|匹配).{0,12}(用户|考过)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _question_reject_reason(item: dict) -> str:
    """诊断用：返回一道原始题目无法通过校验的首个原因，正常路径不依赖它。"""
    qtype = str(item.get("type") or "").strip().lower()
    if qtype not in VALID_TYPES:
        return "bad_type"
    if not str(item.get("stem") or "").strip():
        return "empty_stem"
    options = item.get("options") or []
    answers = item.get("answer") or []
    if len(options) < 2:
        return "few_options"
    valid_keys = {
        str(option.get("key") or "").strip().upper()
        for option in options
        if isinstance(option, dict)
    }
    if not answers:
        return "empty_answer"
    if any(str(answer).strip().upper() not in valid_keys for answer in answers):
        return "answer_key_unknown"
    if len(answers) == len(options):
        return "answer_all_options"
    if qtype == "judge" and len(answers) != 1:
        return "judge_answer_not_single"
    if qtype == "single" and len(answers) != 1:
        return "single_answer_not_single"
    if qtype == "multiple" and len(answers) < 2:
        return "multiple_downgrade"
    explanation = str(item.get("explanation") or "").strip()
    if _has_unreliable_explanation(explanation):
        return "unreliable_explanation"
    if len(explanation) < MIN_FALLBACK_EXPLANATION_CHARS:
        return "explanation_too_short"
    if len(explanation) < MIN_STRICT_EXPLANATION_CHARS:
        return "short_explanation_recoverable"
    return "ok"


def _normalise_question(item: dict, index: int, topic_catalog: dict | None = None) -> dict | None:
    return _normalise_question_with_mode(item, index, topic_catalog, strict=True)


def _normalise_question_with_mode(
    item: dict,
    index: int,
    topic_catalog: dict | None,
    *,
    strict: bool,
) -> dict | None:
    question = _normalise_question_core(item, index, topic_catalog)
    if question:
        return question

    # 严格校验失败时只放宽解析长度，不放宽答案、题型和题干结构。
    # 这层兜底仅在补题后仍不足时使用，避免模型偶尔写短解析导致整份卷子作废。
    explanation = str(item.get("explanation") or "").strip()
    if strict or len(explanation) < MIN_FALLBACK_EXPLANATION_CHARS:
        return None
    relaxed_item = dict(item)
    relaxed_item["explanation"] = explanation
    return _normalise_question_core(
        relaxed_item,
        index,
        topic_catalog,
        min_explanation_chars=MIN_FALLBACK_EXPLANATION_CHARS,
    )


def _normalise_question_core(
    item: dict,
    index: int,
    topic_catalog: dict | None = None,
    min_explanation_chars: int = MIN_STRICT_EXPLANATION_CHARS,
) -> dict | None:
    qtype = str(item.get("type") or "").strip().lower()
    if qtype not in VALID_TYPES:
        return None
    stem = str(item.get("stem") or "").strip()
    if not stem:
        return None

    options: list[dict] = []
    seen_keys: set[str] = set()
    for option in item.get("options") or []:
        if not isinstance(option, dict):
            continue
        key = str(option.get("key") or "").strip().upper()
        text = str(option.get("text") or "").strip()
        if not key or not text or key in seen_keys:
            continue
        seen_keys.add(key)
        options.append({"key": key, "text": text})

    if qtype == "judge" and len(options) != 2:
        options = [{"key": "A", "text": "正确"}, {"key": "B", "text": "错误"}]
        seen_keys = {"A", "B"}

    if len(options) < 2:
        return None

    answer = []
    for value in item.get("answer") or []:
        key = str(value).strip().upper()
        if key in seen_keys and key not in answer:
            answer.append(key)
    answer.sort()
    if not answer or len(answer) == len(options):
        # 没有答案、或全部选项都是答案，都说明这道题不可用。
        return None
    if qtype in ("single", "judge") and len(answer) != 1:
        return None
    if qtype == "multiple" and len(answer) < 2:
        # 多选题只有一个答案属于命题错误，降级为单选题而不是丢弃。
        qtype = "single"

    difficulty = str(item.get("difficulty") or "medium").strip().lower()
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"

    explanation = str(item.get("explanation") or "").strip()
    if len(explanation) < min_explanation_chars or _has_unreliable_explanation(explanation):
        return None

    return {
        "question_id": f"q{index}",
        "type": qtype,
        "stem": stem,
        "options": options,
        "answer": answer,
        "explanation": explanation,
        "topic": _canonical_topic(str(item.get("topic") or "未分类"), topic_catalog),
        "difficulty": difficulty,
        "source_title": str(item.get("source_title") or "").strip(),
    }


def generate_paper(
    keywords: list[str],
    doc_ids: list[str] | None = None,
    single: int = 5,
    multiple: int = 3,
    judge: int = 2,
    difficulty: str = "mixed",
    focus_weak: bool = True,
) -> dict:
    keywords = [k.strip() for k in keywords if k.strip()]
    counts = {"single": max(0, single), "multiple": max(0, multiple), "judge": max(0, judge)}
    total = sum(counts.values())
    if total <= 0:
        raise QuizError("至少要出一道题。")
    if total > MAX_QUESTIONS:
        raise QuizError(f"单份试卷最多 {MAX_QUESTIONS} 道题。")

    material, used_titles, used_docs, used_chunks = _collect_material(keywords, doc_ids or [])
    if not material.strip():
        raise QuizError("没有检索到相关资料，请更换关键词或先导入知识库文档。")

    topic_catalog = _topic_catalog()
    keywords = [_canonical_topic(keyword, topic_catalog) for keyword in keywords]
    weak_topics = [item["topic"] for item in list_weak_topics(limit=8)] if focus_weak else []
    coverage_hint = _topic_type_coverage_hint(keywords)
    recent_stems = _recent_stems(limit=80)
    raw_title = ""
    raw_item_pool: list[dict] = []

    def normalise_raw(raw: dict) -> list[dict]:
        nonlocal raw_title
        raw_title = str(raw.get("title") or raw_title or "").strip()
        normalised: list[dict] = []
        items = raw.get("questions") or []
        raw_item_pool.extend(item for item in items if isinstance(item, dict))
        for item in items:
            if isinstance(item, dict):
                question = _normalise_question(item, len(normalised) + 1, topic_catalog)
                if question:
                    normalised.append(question)
        quiz_logger.info(
            "paper normalise raw=%d strict_ok=%d",
            len(items),
            len(normalised),
        )
        return normalised

    try:
        raw = _request_quiz_raw(
            material,
            keywords,
            counts,
            difficulty,
            recent_stems[:50],
            weak_topics,
            coverage_hint,
        )
        initial_candidates = normalise_raw(raw)
        unique_questions = _dedupe_questions(initial_candidates, recent_stems)
    except llm_service.LlmError as exc:
        raise QuizError(f"组卷失败：{exc}") from exc

    retry_candidates: list[dict] = []
    retry_error: llm_service.LlmError | None = None
    selected: list[dict] = []
    deficits = dict(counts)
    for qtype in VALID_TYPES:
        available = [item for item in unique_questions if item["type"] == qtype]
        chosen = available[: counts[qtype]]
        selected.extend(chosen)
        deficits[qtype] -= len(chosen)

    internal_candidates = _dedupe_questions(initial_candidates, [], accepted=[])
    can_fill_from_initial = all(
        sum(1 for item in internal_candidates if item["type"] == qtype)
        >= counts[qtype]
        for qtype in VALID_TYPES
    )

    # 有效题不足时才补题；若只是历史相似度高，后面的兜底复用首轮结果，避免多等一轮。
    if any(deficits.values()) and not can_fill_from_initial:
        retry_counts = {qtype: max(0, count) for qtype, count in deficits.items()}
        retry_avoid = recent_stems + [item["stem"] for item in selected]
        try:
            retry_raw = _request_quiz_raw(
                material,
                keywords,
                _buffered_counts(retry_counts, buffer_size=2),
                difficulty,
                retry_avoid[-50:],
                weak_topics,
                coverage_hint,
            )
            retry_candidates = normalise_raw(retry_raw)
            unique_questions = _dedupe_questions(
                retry_candidates,
                retry_avoid,
                accepted=unique_questions,
            )
            selected_ids = {id(item) for item in selected}
            for qtype in VALID_TYPES:
                available = [
                    item
                    for item in unique_questions
                    if item["type"] == qtype and id(item) not in selected_ids
                ]
                chosen = available[: deficits[qtype]]
                selected.extend(chosen)
                deficits[qtype] -= len(chosen)
        except llm_service.LlmError as exc:
            retry_error = exc

    quiz_logger.info(
        "paper plan keywords=%s total=%d initial_ok=%d deficits=%s",
        ",".join(keywords[:5]),
        total,
        len(selected),
        deficits,
    )

    # 严格避重两次后仍凑不齐时，优先保证可练习；仍保留当前试卷内部去重。
    if any(deficits.values()):
        fallback_pool = _dedupe_questions(
            initial_candidates + retry_candidates,
            [],
            accepted=list(selected),
            recent_threshold=1.0 + 1e-9,
        )
        for qtype in VALID_TYPES:
            if deficits[qtype] <= 0:
                continue
            available = [
                item
                for item in fallback_pool[len(selected):]
                if item["type"] == qtype
            ]
            chosen = available[: deficits[qtype]]
            selected.extend(chosen)
            deficits[qtype] -= len(chosen)

    # 随机坏批次兜底：宽松层救不回的题（答案错误、自我纠正式解析）只能靠新题，
    # 这里再做一次定向补题；正常一次通过的卷子不会触发这一步。
    if any(deficits.values()):
        retry2_counts = _buffered_counts(
            {qtype: max(0, int(deficits[qtype])) for qtype in VALID_TYPES},
            buffer_size=2,
        )
        retry2_avoid = recent_stems + [item["stem"] for item in selected]
        try:
            retry2_raw = _request_quiz_raw(
                material,
                keywords,
                retry2_counts,
                difficulty,
                retry2_avoid[-50:],
                weak_topics,
                coverage_hint,
            )
            retry2_candidates = normalise_raw(retry2_raw)
            unique_questions = _dedupe_questions(
                retry2_candidates,
                retry2_avoid,
                accepted=unique_questions,
            )
            selected_ids = {id(item) for item in selected}
            for qtype in VALID_TYPES:
                available = [
                    item
                    for item in unique_questions
                    if item["type"] == qtype and id(item) not in selected_ids
                ]
                chosen = available[: deficits[qtype]]
                selected.extend(chosen)
                deficits[qtype] -= len(chosen)
        except llm_service.LlmError as exc:
            quiz_logger.warning("paper retry2 failed: %s", exc)

    # 严格解析门槛救回来的题仍不够时，最后把原始题目用宽松解析重新归一化。
    # 这里仍然拒绝结构错误、答案错误和模型自我纠正式的不可靠解析。
    if any(deficits.values()) or len(selected) != total:
        relaxed_candidates: list[dict] = []
        for item in raw_item_pool:
            question = _normalise_question_with_mode(
                item,
                len(relaxed_candidates) + 1,
                topic_catalog,
                strict=False,
            )
            if question:
                relaxed_candidates.append(question)
        quiz_logger.info(
            "paper relaxed pool raw=%d recovered=%d deficits=%s",
            len(raw_item_pool),
            len(relaxed_candidates),
            deficits,
        )
        relaxed_pool = _dedupe_questions(
            relaxed_candidates,
            [],
            accepted=list(selected),
            recent_threshold=1.0 + 1e-9,
        )
        for qtype in VALID_TYPES:
            if deficits[qtype] <= 0:
                continue
            available = [
                item
                for item in relaxed_pool[len(selected):]
                if item["type"] == qtype
            ]
            chosen = available[: deficits[qtype]]
            selected.extend(chosen)
            deficits[qtype] -= len(chosen)

    if any(deficits.values()) or len(selected) != total:
        reject_summary = [
            reason
            for reason in (_question_reject_reason(item) for item in raw_item_pool)
            if reason != "ok"
        ][:12]
        quiz_logger.warning(
            "paper incomplete selected=%d/%d deficits=%s rejects=%s",
            len(selected),
            total,
            deficits,
            reject_summary,
        )
        if retry_error is not None:
            raise QuizError(f"补充命题失败：{retry_error}") from retry_error
        raise QuizError("模型返回的有效题目不足，请稍后重试或更换关键词。")

    questions = []
    type_order = {qtype: index for index, qtype in enumerate(VALID_TYPES)}
    selected.sort(key=lambda item: type_order[item["type"]])
    for index, question in enumerate(selected, start=1):
        question["question_id"] = f"q{index}"
        questions.append(question)

    used_doc_map = {_title_key(doc["title"]): doc for doc in used_docs if doc.get("title")}
    for question in questions:
        matched = used_doc_map.get(_title_key(question.get("source_title") or ""))
        if matched:
            question["source_doc_id"] = matched["doc_id"]
            question["source_docs"] = [dict(matched)]
            question["source_chunk_ids"] = [
                chunk["chunk_id"]
                for chunk in used_chunks
                if chunk.get("doc_id") == matched["doc_id"]
            ]

    # used_docs 是命题候选材料；试卷头必须只展示最终入选题目实际依据的文档，
    # 避免用户看到“来源混入了其他文档”的误导。
    question_doc_ids = {
        str(question.get("source_doc_id") or "")
        for question in questions
        if question.get("source_doc_id")
    }
    actual_docs = [doc for doc in used_docs if doc.get("doc_id") in question_doc_ids]
    actual_titles = [doc["title"] for doc in actual_docs if doc.get("title")]
    actual_chunks = [
        chunk for chunk in used_chunks if chunk.get("doc_id") in question_doc_ids
    ]

    paper_id = uuid.uuid4().hex
    title = raw_title
    if not title:
        title = ("、".join(keywords[:3]) or "综合练习") + " 测验"

    paper = {
        "paper_id": paper_id,
        "title": title,
        "keywords": keywords,
        "doc_titles": actual_titles,
        "doc_ids": [doc["doc_id"] for doc in actual_docs],
        "source_docs": actual_docs,
        "source_chunks": actual_chunks,
        "difficulty": difficulty,
        "questions": questions,
        "created_at": int(time.time()),
    }
    _write_json(PAPERS_DIR / f"{paper_id}.json", paper)
    return paper


def _latest_attempt_id() -> str:
    ensure_dirs()
    paths = sorted(
        ATTEMPTS_DIR.glob("*.json"),
        key=lambda path: (
            _read_json(path, {}).get("created_at") or 0,
            path.stat().st_mtime,
        ),
    )
    return paths[-1].stem if paths else ""


def _current_mistake_topics(attempt_id: str, limit: int) -> list[str]:
    attempt_id = attempt_id or _latest_attempt_id()
    if not attempt_id:
        raise QuizError("还没有可用于重做的答卷，请先完成一套题。")
    attempt = get_attempt(attempt_id)
    topics: list[str] = []
    for item in attempt.get("questions") or []:
        if item.get("is_correct"):
            continue
        topic = str(item.get("topic") or "未分类")
        if topic not in topics:
            topics.append(topic)
        if len(topics) >= limit:
            break
    if not topics:
        raise QuizError("这份答卷没有错题，可以切换累计错题或重新选择知识点组卷。")
    return topics


def _all_mistake_topics(limit: int) -> list[str]:
    mistakes = list_mistakes()
    if not mistakes:
        raise QuizError("累计错题本是空的，先去做一份测验吧。")
    topics: list[str] = []
    for item in mistakes:
        topic = item.get("topic")
        if topic and topic not in topics:
            topics.append(topic)
        if len(topics) >= limit:
            break
    return topics


def _filter_mistakes_by_time(
    mistakes: list[dict],
    time_range: str,
    attempt_id: str = "",
) -> tuple[list[dict], str]:
    """按时间档位过滤错题；last 表示最近一次答卷对应的错题。"""
    if time_range == "last":
        resolved_attempt_id = attempt_id or _latest_attempt_id()
        if not resolved_attempt_id:
            raise QuizError("还没有可用于重做的答卷，请先完成一套题。")
        attempt = get_attempt(resolved_attempt_id)
        paper_id = str(attempt.get("paper_id") or "")
        if not paper_id:
            raise QuizError("最近答卷缺少试卷编号，无法定位上次错题。")
        filtered = [item for item in mistakes if str(item.get("paper_id") or "") == paper_id]
        return filtered, resolved_attempt_id

    if time_range in ("1d", "7d"):
        window_seconds = 86_400 if time_range == "1d" else 7 * 86_400
        threshold = int(time.time()) - window_seconds
        filtered = [
            item
            for item in mistakes
            if max(int(item.get("last_seen_at") or 0), int(item.get("created_at") or 0)) >= threshold
        ]
        return filtered, attempt_id

    return mistakes, attempt_id


def _type_preserving_plan(
    mistakes: list[dict], total: int
) -> tuple[dict[str, int], list[dict]]:
    """优先复现错题题型分布；可用错题不足时按原比例扩容。"""
    ordered = sorted(
        mistakes,
        key=lambda item: (
            int(item.get("wrong_count") or 0),
            int(item.get("last_seen_at") or 0),
            int(item.get("created_at") or 0),
        ),
        reverse=True,
    )
    picked = ordered[:total]
    counts = {qtype: 0 for qtype in VALID_TYPES}
    for item in picked:
        qtype = str(item.get("type") or "").strip().lower()
        counts[qtype if qtype in counts else "single"] += 1

    base_total = sum(counts.values())
    if base_total and base_total < total:
        remaining = total - base_total
        quotas = {
            qtype: counts[qtype] / base_total * remaining
            for qtype in VALID_TYPES
            if counts[qtype]
        }
        for qtype, quota in quotas.items():
            counts[qtype] += int(quota)
            quotas[qtype] = quota - int(quota)
        for qtype, _ in sorted(
            quotas.items(),
            key=lambda pair: (-pair[1], VALID_TYPES.index(pair[0])),
        )[: max(0, total - sum(counts.values()))]:
            counts[qtype] += 1

    return counts, picked


def _mistake_item_doc_ids(items: list[dict]) -> list[str]:
    """从本次实际选中的错题收集来源文档，不向无关资料扩散。"""
    valid_doc_ids = {
        doc["doc_id"] for doc in knowledge_service.list_documents() if doc.get("doc_id")
    }
    doc_ids: list[str] = []
    for item in items:
        candidates = [str(item.get("source_doc_id") or "")]
        candidates.extend(
            str(doc.get("doc_id") or "") for doc in item.get("source_docs") or []
        )
        for doc_id in candidates:
            if doc_id and doc_id in valid_doc_ids and doc_id not in doc_ids:
                doc_ids.append(doc_id)
    return doc_ids


def generate_mistake_paper(
    limit: int = 8,
    difficulty: str = "mixed",
    scope: str = "current",
    attempt_id: str = "",
    time_range: str = "all",
    topics: list[str] | None = None,
    total: int | None = None,
) -> dict:
    """按本次错题或累计错题的知识点重新生成变形题。"""
    source_attempt_id = attempt_id or (_latest_attempt_id() if scope == "current" else "")
    if scope == "current":
        topics = _current_mistake_topics(attempt_id, limit)
        doc_ids = _mistake_source_doc_ids(topics)
        counts = {
            "single": max(2, min(5, len(topics))),
            "multiple": 2,
            "judge": 2,
        }
        if sum(counts.values()) > MAX_QUESTIONS:
            counts["judge"] = max(0, MAX_QUESTIONS - counts["single"] - counts["multiple"])
    else:
        mistakes = list_mistakes(limit=10_000)
        if not mistakes:
            raise QuizError("累计错题本是空的，先去做一份测验吧。")
        filtered, source_attempt_id = _filter_mistakes_by_time(
            mistakes, time_range, attempt_id
        )
        wanted_topics = {_normalise_topic_key(item) for item in (topics or []) if item}
        if wanted_topics:
            filtered = [
                item
                for item in filtered
                if _normalise_topic_key(str(item.get("topic") or "")) in wanted_topics
            ]
        if not filtered:
            raise QuizError("所选时间或知识点范围内没有错题，请调整范围。")

        requested_total = total if total is not None else max(1, limit)
        counts, picked_mistakes = _type_preserving_plan(filtered, requested_total)
        topics = []
        for item in picked_mistakes:
            topic = str(item.get("topic") or "未分类")
            if topic not in topics:
                topics.append(topic)
        doc_ids = _mistake_item_doc_ids(picked_mistakes)
        if not doc_ids:
            doc_ids = _mistake_source_doc_ids(topics)

    paper = generate_paper(
        topics,
        doc_ids,
        single=counts["single"],
        multiple=counts["multiple"],
        judge=counts["judge"],
        difficulty=difficulty,
        focus_weak=True,
    )
    paper["generation_context"] = {
        "kind": "mistake_redo",
        "scope": scope,
        "source_attempt_id": source_attempt_id,
        "time_range": time_range if scope == "all" else "",
        "requested_total": total if scope == "all" and total is not None else None,
        "selected_topics": list(topics or []),
        "question_type_plan": dict(counts),
    }
    _write_json(PAPERS_DIR / f"{paper['paper_id']}.json", paper)
    return paper


def get_paper(paper_id: str, include_answers: bool = False) -> dict:
    paper = _read_json(PAPERS_DIR / f"{paper_id}.json", None)
    if not paper:
        raise QuizError("试卷不存在或已过期。")
    if include_answers:
        return paper
    public = dict(paper)
    public["questions"] = [
        {key: value for key, value in question.items() if key not in ("answer", "explanation")}
        for question in paper["questions"]
    ]
    return public


def list_papers(limit: int = 20) -> list[dict]:
    ensure_dirs()
    items: list[dict] = []
    for path in PAPERS_DIR.glob("*.json"):
        paper = _read_json(path, None)
        if not paper:
            continue
        items.append(
            {
                "paper_id": paper["paper_id"],
                "title": paper.get("title", ""),
                "keywords": paper.get("keywords") or [],
                "question_count": len(paper.get("questions") or []),
                "created_at": paper.get("created_at", 0),
            }
        )
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items[:limit]


def _recent_stems(limit: int = 80) -> list[str]:
    stems: list[str] = []
    for paper in list_papers(limit=10):
        full = _read_json(PAPERS_DIR / f"{paper['paper_id']}.json", None)
        if not full:
            continue
        for question in full.get("questions") or []:
            stem = question.get("stem")
            if stem:
                stems.append(stem)
            if len(stems) >= limit:
                return stems
    return stems


def _topic_type_coverage_hint(topics: list[str]) -> list[str]:
    """指出每个待练知识点已经通过哪些题型验证，供命题时换题型。"""
    mastery = _ensure_mastery_schema()
    type_labels = {"single": "单选题", "multiple": "多选题", "judge": "判断题"}
    hints: list[str] = []
    for topic in topics:
        stat = mastery.get(topic)
        if not stat:
            hints.append(f"- {topic}：历史样本较少，三种题型均可覆盖。")
            continue
        verified = [
            type_labels[qtype]
            for qtype in VALID_TYPES
            if stat.get("by_type", {}).get(qtype, {}).get("correct", 0) > 0
        ]
        pending = [
            type_labels[qtype]
            for qtype in VALID_TYPES
            if stat.get("by_type", {}).get(qtype, {}).get("correct", 0) == 0
        ]
        if pending:
            hints.append(
                f"- {topic}：已答对 {('、'.join(verified) or '暂无')}，"
                f"下一次优先用 {'、'.join(pending[:2])} 换场景考查，不要复用旧题干。"
            )
        elif len(verified) >= 2:
            hints.append(f"- {topic}：已通过多题型验证，可少量出间隔复习题。")
    return hints[:10]


# --------------------------------------------------------------------------
# 判分
# --------------------------------------------------------------------------


def _answer_text(question: dict, keys: list[str]) -> str:
    lookup = {option["key"]: option["text"] for option in question.get("options") or []}
    return "；".join(f"{key}. {lookup.get(key, '')}" for key in keys) if keys else ""


def _mistake_source_doc_ids(topics: list[str]) -> list[str]:
    """错题重练只引用这些知识点对应错题的原始来源文档；无线索时回退全库。"""
    valid_doc_ids = {
        doc["doc_id"] for doc in knowledge_service.list_documents() if doc.get("doc_id")
    }
    wanted_topics = set(topics)
    doc_ids: list[str] = []
    for mistake in list_mistakes(limit=10_000):
        if str(mistake.get("topic") or "") not in wanted_topics:
            continue
        doc_id = str(mistake.get("source_doc_id") or "")
        if doc_id and doc_id in valid_doc_ids and doc_id not in doc_ids:
            doc_ids.append(doc_id)
    return doc_ids


def _accuracy(correct: int, total: int) -> float:
    return round(correct / total, 3) if total else 0.0


def _correct_types(stat: dict) -> list[str]:
    return [
        qtype
        for qtype in VALID_TYPES
        if stat.get("by_type", {}).get(qtype, {}).get("correct", 0) > 0
    ]


def _graded_type_stats(graded: list[dict]) -> list[dict]:
    """统计当前答卷的题型表现，不混入历史掌握度。"""
    stats = {qtype: {"total": 0, "correct": 0} for qtype in VALID_TYPES}
    for item in graded:
        qtype = str(item.get("type") or "").strip().lower()
        if qtype not in stats:
            continue
        stats[qtype]["total"] += 1
        stats[qtype]["correct"] += 1 if item.get("is_correct") else 0
    return [
        {
            "type": qtype,
            "label": TYPE_LABELS[qtype],
            "total": stats[qtype]["total"],
            "correct": stats[qtype]["correct"],
            "accuracy": _accuracy(stats[qtype]["correct"], stats[qtype]["total"]),
        }
        for qtype in VALID_TYPES
    ]


def _current_focus_entry(topic: str, items: list[dict], catalog: dict | None = None) -> dict:
    """错题变形卷提交后，只按本卷表现生成重点复习项。"""
    module = _topic_module(topic, catalog or _topic_catalog())
    by_type = {qtype: {"total": 0, "correct": 0} for qtype in VALID_TYPES}
    wrong_types: list[str] = []
    total = 0
    correct = 0
    for item in items:
        qtype = str(item.get("type") or "").strip().lower()
        if qtype not in by_type:
            continue
        is_correct = bool(item.get("is_correct"))
        by_type[qtype]["total"] += 1
        by_type[qtype]["correct"] += 1 if is_correct else 0
        total += 1
        correct += 1 if is_correct else 0
        if not is_correct and qtype not in wrong_types:
            wrong_types.append(qtype)

    entry = _topic_entry(
        topic,
        {"total": total, "correct": correct, "streak": 0, "by_type": by_type},
        module,
    )
    wrong_labels = [TYPE_LABELS[qtype] for qtype in wrong_types]
    reasons = ["本卷有答错题目"]
    if total < 3:
        reasons.append("本卷样本不足 3 题")
    elif entry["accuracy"] < 0.6:
        reasons.append("本卷正确率低于 60%")
    elif entry["accuracy"] < 0.8:
        reasons.append("本卷正确率低于 80%")

    if entry["total"] >= 2 and not entry["cross_type_verified"]:
        passed = [TYPE_LABELS[qtype] for qtype in entry["verified_types"]]
        reasons.append(
            f"本卷仅通过 {'、'.join(passed) if passed else '无'} 验证，建议继续换题型确认"
        )

    if wrong_types:
        recommended = (
            f"先看本卷解析，优先重练{'、'.join(wrong_labels)}；"
            "理解后再用已答对题型做一次间隔验证"
        )
    else:
        recommended = "本卷未暴露该知识点错误，可作为间隔复习项"
    entry["reasons"] = reasons
    entry["missing_types"] = wrong_types
    entry["recommended_action"] = recommended
    return entry


def _topic_entry(topic: str, stat: dict, module: str = "") -> dict:
    total = int(stat.get("total", 0))
    correct = int(stat.get("correct", 0))
    accuracy = _accuracy(correct, total)
    verified_types = _correct_types(stat)
    return {
        "topic": topic,
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "streak": int(stat.get("streak", 0)),
        "level": _local_mastery_level(accuracy),
        "module": module,
        "by_type": stat.get("by_type", {}),
        "verified_types": verified_types,
        "cross_type_verified": len(verified_types) >= 2,
    }


def _is_mastered_topic(entry: dict) -> bool:
    return (
        entry["total"] >= 4
        and entry["accuracy"] >= 0.85
        and entry["streak"] >= 2
        and entry["cross_type_verified"]
    )


def _aggregate_type_stats(mastery: dict, topics: set[str] | None = None) -> list[dict]:
    stats = {qtype: {"total": 0, "correct": 0} for qtype in VALID_TYPES}
    for topic, stat in mastery.items():
        if topics is not None and topic not in topics:
            continue
        for qtype in VALID_TYPES:
            current = stat.get("by_type", {}).get(qtype, {})
            stats[qtype]["total"] += int(current.get("total", 0))
            stats[qtype]["correct"] += int(current.get("correct", 0))
    return [
        {
            "type": qtype,
            "label": TYPE_LABELS[qtype],
            "total": stats[qtype]["total"],
            "correct": stats[qtype]["correct"],
            "accuracy": _accuracy(stats[qtype]["correct"], stats[qtype]["total"]),
        }
        for qtype in VALID_TYPES
    ]


def _module_stats(
    mastery: dict,
    catalog: dict,
    active_mistake_topics: set[str] | None = None,
) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    active_mistake_topics = active_mistake_topics or set()
    for topic, stat in mastery.items():
        if int(stat.get("total", 0)) <= 0:
            continue
        module = _topic_module(topic, catalog)
        entry = _topic_entry(topic, stat, module)
        groups.setdefault(module, []).append(entry)

    modules: list[dict] = []
    for module, entries in groups.items():
        topics = {entry["topic"] for entry in entries}
        type_stats = _aggregate_type_stats(mastery, topics)
        total = sum(entry["total"] for entry in entries)
        correct = sum(entry["correct"] for entry in entries)
        mastered_entries = [entry for entry in entries if _is_mastered_topic(entry)]
        weak_entries = [
            entry
            for entry in entries
            if not _is_mastered_topic(entry) or entry["topic"] in active_mistake_topics
        ]
        weakest_type = min(type_stats, key=lambda item: (item["accuracy"], -item["total"]))
        active_count = len(topics & active_mistake_topics)
        accuracy = _accuracy(correct, total)
        if active_count:
            recommendation = f"先清掉 {active_count} 个错题本知识点，再做一套混合卷。"
        elif weakest_type["total"] == 0:
            recommendation = f"补充未练过的{weakest_type['label']}，验证同一知识能否换题型表达。"
        elif weakest_type["accuracy"] < 0.8:
            recommendation = f"优先补强{weakest_type['label']}，当前该题型正确率为 {round(weakest_type['accuracy'] * 100)}%。"
        elif weak_entries:
            recommendation = "围绕尚未跨题型掌握的知识点，换业务场景继续练习。"
        else:
            recommendation = "掌握稳定，保持间隔复习即可。"
        status = (
            "mastered"
            if total >= 8
            and accuracy >= 0.85
            and not active_count
            and len(mastered_entries) >= max(1, len(entries) * 0.7)
            else "developing"
            if accuracy >= 0.6
            else "beginner"
        )
        modules.append(
            {
                "module": module,
                "total": total,
                "correct": correct,
                "accuracy": accuracy,
                "topic_count": len(entries),
                "mastered_topic_count": len(mastered_entries),
                "weak_topic_count": len(weak_entries),
                "active_mistake_count": active_count,
                "mastered_ratio": round(len(mastered_entries) / len(entries), 3) if entries else 0,
                "type_stats": type_stats,
                "status": status,
                "recommendation": recommendation,
            }
        )
    modules.sort(key=lambda item: (item["status"] == "mastered", item["accuracy"], -item["total"]))
    return modules


def _focus_entry(
    topic: str,
    stat: dict,
    wrong_topics: set[str],
    active_mistake_topics: set[str],
    catalog: dict | None = None,
) -> dict | None:
    module = _topic_module(topic, catalog or _topic_catalog())
    entry = _topic_entry(topic, stat, module)
    reasons: list[str] = []
    if topic in wrong_topics:
        reasons.append("本卷有答错题目")
    elif topic in active_mistake_topics:
        reasons.append("错题本中尚未连续答对两次")

    if entry["total"] < 3:
        reasons.append("历史样本不足 3 题")
    elif entry["accuracy"] < 0.6:
        reasons.append("累计正确率低于 60%")
    elif entry["accuracy"] < 0.8:
        reasons.append("累计正确率低于 80%")

    missing_types = [
        qtype
        for qtype in VALID_TYPES
        if entry["by_type"].get(qtype, {}).get("correct", 0) == 0
    ]
    if entry["total"] >= 2 and not entry["cross_type_verified"]:
        labels = [TYPE_LABELS[qtype] for qtype in missing_types]
        reasons.append(f"还没有通过 {'、'.join(labels[:2])} 验证")

    if not reasons:
        return None

    recommended = "先回读对应资料，再用未验证题型重做一轮"
    if topic in wrong_topics:
        recommended = "先看错题解析，再用不同题型重做同一知识点"
    elif missing_types:
        recommended = f"下一套优先用 {'、'.join(TYPE_LABELS[t] for t in missing_types[:2])} 换场景考查"
    entry["reasons"] = reasons
    entry["missing_types"] = missing_types
    entry["recommended_action"] = recommended
    return entry


def _next_paper_suggestion(focus_topics: list[dict], type_stats: list[dict]) -> dict:
    keywords = [str(item["topic"]) for item in focus_topics[:6]]
    gap_counts = {qtype: 0 for qtype in VALID_TYPES}
    for item in focus_topics:
        for qtype in item.get("missing_types", []):
            gap_counts[qtype] += 1

    ordered_types = sorted(
        VALID_TYPES,
        key=lambda qtype: (
            -gap_counts[qtype],
            next(stat["accuracy"] for stat in type_stats if stat["type"] == qtype),
            next(stat["total"] for stat in type_stats if stat["type"] == qtype),
        ),
    )
    allocations = (8, 7, 5)
    counts = {qtype: 0 for qtype in VALID_TYPES}
    for qtype, count in zip(ordered_types, allocations):
        counts[qtype] = count
    weakest_label = TYPE_LABELS[ordered_types[0]] if ordered_types else "综合"
    return {
        "keywords": keywords,
        "single": counts["single"],
        "multiple": counts["multiple"],
        "judge": counts["judge"],
        "reason": f"下一套建议共 20 题，优先增加{weakest_label}，并围绕前 6 个薄弱知识点换题型考查。",
    }


def _build_learning_guide(graded: list[dict] | None = None, paper: dict | None = None) -> dict:
    graded = graded or []
    generation_context = paper.get("generation_context") if paper else None
    is_mistake_redo = (
        isinstance(generation_context, dict)
        and generation_context.get("kind") == "mistake_redo"
    )
    mastery = _ensure_mastery_schema()
    catalog = _topic_catalog()
    mistakes = list_mistakes(limit=10_000)
    active_mistake_topics = {str(item.get("topic") or "未分类") for item in mistakes}
    wrong_topics = {
        str(item.get("topic") or "未分类")
        for item in graded
        if not item.get("is_correct")
    }
    current_topics = list(dict.fromkeys(str(item.get("topic") or "未分类") for item in graded))

    if is_mistake_redo:
        items_by_topic: dict[str, list[dict]] = {}
        for item in graded:
            items_by_topic.setdefault(str(item.get("topic") or "未分类"), []).append(item)
        focus_topics = [
            _current_focus_entry(topic, items_by_topic[topic], catalog)
            for topic in current_topics
            if topic in wrong_topics and topic in items_by_topic
        ]
        focus_topics.sort(key=lambda item: (item["accuracy"], -item["total"]))
        type_stats = _graded_type_stats(graded)

        suggestion_topics = focus_topics
        if not suggestion_topics:
            suggestion_topics = [
                _current_focus_entry(topic, items_by_topic[topic], catalog)
                for topic in current_topics
                if topic in items_by_topic
            ]
        next_paper = _next_paper_suggestion(suggestion_topics, type_stats)
        if not wrong_topics:
            next_paper["reason"] = "本卷变形题已全部答对，下一套建议围绕本卷知识点做间隔复习。"

        module_stats = _module_stats(mastery, catalog, active_mistake_topics)
        all_entries = [
            _topic_entry(topic, stat, _topic_module(topic, catalog))
            for topic, stat in mastery.items()
        ]
        mastered_topics = [entry for entry in all_entries if _is_mastered_topic(entry)]
        mastered_topics.sort(key=lambda item: (item["topic"] not in current_topics, -item["total"]))
        answered_total = sum(item["total"] for item in all_entries)
        correct_total = sum(item["correct"] for item in all_entries)
        overall_accuracy = _accuracy(correct_total, answered_total)
        long_term_type_stats = _aggregate_type_stats(mastery)
        weak_type_stats = [
            item for item in long_term_type_stats
            if item["total"] == 0 or item["accuracy"] < 0.8
        ]
        interview_ready = (
            answered_total >= 20
            and overall_accuracy >= 0.85
            and not active_mistake_topics
            and not weak_type_stats
            and len(mastered_topics) >= 5
            and len(mastered_topics) >= len(all_entries) * 0.7
        )
        summary = (
            f"本卷变形练习发现 {len(focus_topics)} 个知识点需要继续巩固；"
            "右侧就绪度仍按累计学习数据计算。"
        )
        return {
            "summary": summary,
            "type_stats": type_stats,
            "module_stats": module_stats,
            "mastered_topics": mastered_topics[:12],
            "focus_topics": focus_topics[:8],
            "next_paper": next_paper,
            "interview_ready": interview_ready,
            "interview_reasons": [],
            "scope_note": "该就绪度只按已练习知识点计算，不会锁定模拟面试入口。",
            "focus_scope_note": "下一步重点仅统计本卷错题；就绪度仍使用累计掌握度。",
        }

    all_entries = [
        _topic_entry(topic, stat, _topic_module(topic, catalog))
        for topic, stat in mastery.items()
    ]
    mastered_topics = [entry for entry in all_entries if _is_mastered_topic(entry)]
    mastered_topics.sort(key=lambda item: (item["topic"] not in current_topics, -item["total"]))

    weak_topics = {item["topic"] for item in list_weak_topics(limit=100, threshold=0.8)}
    candidate_topics = list(
        dict.fromkeys(current_topics + sorted(active_mistake_topics | weak_topics))
    )
    focus_topics: list[dict] = []
    for topic in candidate_topics:
        stat = mastery.get(topic)
        if not stat:
            continue
        entry = _focus_entry(topic, stat, wrong_topics, active_mistake_topics, catalog)
        if entry:
            focus_topics.append(entry)
    focus_topics.sort(key=lambda item: (item["topic"] not in wrong_topics, item["accuracy"], -item["total"]))

    type_stats = _aggregate_type_stats(mastery)
    module_stats = _module_stats(mastery, catalog, active_mistake_topics)
    next_paper = _next_paper_suggestion(focus_topics, type_stats)
    if not next_paper["keywords"] and mastered_topics:
        next_paper["keywords"] = [item["topic"] for item in mastered_topics[:6]]
        next_paper["reason"] = "当前薄弱点较少，建议用 20 道混合卷对已掌握知识点做间隔复习。"
    answered_total = sum(item["total"] for item in all_entries)
    correct_total = sum(item["correct"] for item in all_entries)
    overall_accuracy = _accuracy(correct_total, answered_total)
    weak_type_stats = [
        item for item in type_stats if item["total"] == 0 or item["accuracy"] < 0.8
    ]
    interview_ready = (
        answered_total >= 20
        and overall_accuracy >= 0.85
        and not active_mistake_topics
        and not weak_type_stats
        and len(mastered_topics) >= 5
        and len(mastered_topics) >= len(all_entries) * 0.7
    )
    interview_reasons: list[str] = []
    if answered_total < 20:
        interview_reasons.append("累计答题量不足 20 题")
    if overall_accuracy < 0.85:
        interview_reasons.append("整体正确率低于 85%")
    if active_mistake_topics:
        interview_reasons.append("错题本仍有未巩固知识点")
    if weak_type_stats:
        interview_reasons.append("仍有题型正确率低于 80% 或样本不足")
    if not interview_ready and not interview_reasons:
        interview_reasons.append("跨题型已掌握知识点比例不足 70%")

    if graded:
        current_mastered = [item["topic"] for item in all_entries if item["topic"] in current_topics and _is_mastered_topic(item)]
        summary = (
            f"本次后已有 {len(current_mastered)} 个本卷知识点达到跨题型掌握，"
            f"当前还有 {len(focus_topics)} 个知识点建议继续巩固。"
        )
    else:
        summary = (
            f"已练习 {answered_total} 题，跨题型掌握 {len(mastered_topics)} 个知识点，"
            f"下一步建议优先处理 {len(focus_topics)} 个薄弱或题型覆盖不足的知识点。"
        )

    return {
        "summary": summary,
        "type_stats": type_stats,
        "module_stats": module_stats,
        "mastered_topics": mastered_topics[:12],
        "focus_topics": focus_topics[:8],
        "next_paper": next_paper,
        "interview_ready": interview_ready,
        "interview_reasons": interview_reasons,
        "scope_note": "该就绪度只按已练习知识点计算，不会锁定模拟面试入口。",
    }


def _review_focus_items(graded: list[dict], topic_stats: dict[str, dict]) -> list[dict]:
    """只把答错、未作答或历史正确率偏低的题送入复习模型。"""
    focus: list[dict] = []
    for item in graded:
        topic = str(item.get("topic") or "未分类")
        stat = topic_stats.get(topic) or {}
        try:
            accuracy = float(stat.get("correct", 0)) / max(1, int(stat.get("total", 0)))
        except (TypeError, ValueError):
            accuracy = 1.0
        if not item.get("is_correct") or accuracy < 0.8:
            focus.append(item)
    return focus


def _build_review_context(graded: list[dict]) -> str:
    """给分批复习模型补充低成本的整卷概览，避免只见局部错题。"""
    total = len(graded)
    if not total:
        return ""

    correct = sum(1 for item in graded if item.get("is_correct"))
    unanswered = sum(1 for item in graded if not (item.get("user_answer") or []))
    wrong = total - correct
    by_type: dict[str, list[int]] = {
        qtype: [0, 0] for qtype in VALID_TYPES
    }
    topic_stats: dict[str, list[int]] = {}
    for item in graded:
        qtype = item.get("type")
        topic = str(item.get("topic") or "未分类")
        if qtype in by_type:
            by_type[qtype][1] += 1
            if item.get("is_correct"):
                by_type[qtype][0] += 1
        stat = topic_stats.setdefault(topic, [0, 0])
        stat[1] += 1
        if item.get("is_correct"):
            stat[0] += 1

    type_lines = [
        f"{TYPE_LABELS[qtype]} {values[0]}/{values[1]}"
        for qtype, values in by_type.items()
        if values[1]
    ]
    solid_topics = [
        f"{topic}（{values[0]}题）"
        for topic, values in topic_stats.items()
        if values[0] == values[1]
    ]

    lines = [
        f"本卷共 {total} 题：答对 {correct}，答错 {wrong}，未作答 {unanswered}。"
    ]
    if type_lines:
        lines.append("题型表现：" + "；".join(type_lines) + "。")
    lines.append(
        "完全答对的知识点："
        + ("、".join(solid_topics) if solid_topics else "无")
        + "。"
    )
    lines.append(
        "系统会分批逐题分析答错或历史正确率偏低的题；这里只补充整卷概览，"
        "总结整体表现时可使用这些信息，不要误以为分批题目就是全部试卷。"
    )
    return "【整体试卷上下文】\n" + "\n".join(lines)


def _filter_topic_history(history: list[dict], topics: set[str] | None) -> list[dict]:
    if topics is None:
        return history
    return [item for item in history if str(item.get("topic") or "未分类") in topics]


def _filter_review_sources(
    review_sources: dict[str, list[dict]],
    topics: set[str] | None,
) -> dict[str, list[dict]]:
    if topics is None:
        return review_sources
    return {topic: refs for topic, refs in review_sources.items() if topic in topics}


def _build_review_batches(
    graded: list[dict],
    topic_stats: dict[str, dict],
    review_sources: dict[str, list[dict]],
    history: list[dict],
) -> list[dict]:
    """把弱题分成小批次，避免一次复习调用同时承载全部逐题结果和原文。"""
    focus = _review_focus_items(graded, topic_stats)
    focus = _prioritise_review_items(focus, topic_stats)[:REVIEW_LLM_ITEM_LIMIT]
    if len(focus) <= REVIEW_BATCH_SIZE:
        return [
            {
                "items": focus,
                "history": history,
                "sources": review_sources,
            }
        ]

    batches: list[dict] = []
    for offset in range(0, len(focus), REVIEW_BATCH_SIZE):
        items = focus[offset : offset + REVIEW_BATCH_SIZE]
        topics = {str(item.get("topic") or "未分类") for item in items}
        batches.append(
            {
                "items": items,
                "history": _filter_topic_history(history, topics),
                "sources": _filter_review_sources(review_sources, topics),
            }
        )
    return batches


def _prioritise_review_items(
    focus: list[dict],
    topic_stats: dict[str, dict],
) -> list[dict]:
    """错题/未作答优先，其次按历史正确率从低到高排队。"""

    def sort_key(item: dict):
        stat = topic_stats.get(str(item.get("topic") or "未分类")) or {}
        try:
            accuracy = float(stat.get("correct", 0)) / max(1, int(stat.get("total", 0)))
        except (TypeError, ValueError):
            accuracy = 1.0
        unanswered = not (item.get("user_answer") or [])
        return (
            bool(item.get("is_correct")),
            bool(unanswered),
            accuracy,
        )

    return sorted(focus, key=sort_key)


def _merge_review_part(left: dict, right: dict) -> dict:
    def richness(item: dict) -> int:
        return (
            len(str(item.get("diagnosis") or ""))
            + len(str(item.get("plain_summary") or ""))
            + len(str(item.get("analogy") or ""))
            + sum(len(str(value)) for value in item.get("flow_steps") or [])
            + sum(len(str(value)) for value in item.get("study_points") or [])
            + sum(len(str(value)) for value in item.get("next_actions") or [])
        )

    return max([left, right], key=lambda item: (richness(item), bool(item.get("topic"))))


def _merge_reviews(results: list[dict]) -> dict:
    """合并分批复习结果；相同知识点保留信息最完整的一条。"""
    merged = {
        "summary": "",
        "mastery_level": "",
        "can_advance": True,
        "advance_reason": "",
        "weak_topics": [],
        "study_plan": [],
        "encouragement": "",
    }
    weak_by_key: dict[str, dict] = {}
    plan_by_action: dict[str, dict] = {}
    levels: list[str] = []

    for result in results:
        if not isinstance(result, dict):
            continue
        for field in ("summary", "advance_reason", "encouragement"):
            if not merged[field] and str(result.get(field) or "").strip():
                merged[field] = str(result[field]).strip()

        level = str(result.get("mastery_level") or "").strip().lower()
        if level in MASTERY_LEVEL_ORDER:
            levels.append(level)
        merged["can_advance"] = merged["can_advance"] and bool(result.get("can_advance"))

        for item in result.get("weak_topics") or []:
            if not isinstance(item, dict):
                continue
            topic = str(item.get("topic") or "").strip()
            if not topic:
                continue
            key = _normalise_topic_key(topic)
            current = weak_by_key.get(key)
            weak_by_key[key] = _merge_review_part(current or item, item)

        for item in result.get("study_plan") or []:
            if not isinstance(item, dict):
                continue
            action = str(
                item.get("action")
                or item.get("step")
                or item.get("description")
                or ""
            ).strip()
            if not action:
                continue
            key = _normalise_topic_key(action)
            plan_by_action.setdefault(key, item)

    merged["weak_topics"] = list(weak_by_key.values())
    merged["study_plan"] = list(plan_by_action.values())
    if levels:
        merged["mastery_level"] = min(levels, key=lambda level: MASTERY_LEVEL_ORDER[level])
    return merged


def _merge_local_coverage(merged: dict, coverage: dict) -> dict:
    """本地覆盖层只补充模型未覆盖的知识点，避免通用兜底替换深度分析。"""
    known = {
        _normalise_topic_key(str(item.get("topic") or ""))
        for item in merged.get("weak_topics") or []
    }
    coverage = dict(coverage)
    coverage["weak_topics"] = [
        item
        for item in coverage.get("weak_topics") or []
        if _normalise_topic_key(str(item.get("topic") or "")) not in known
    ]
    return _merge_reviews([merged, coverage])


def _generate_review(
    graded: list[dict],
    score: float,
    accuracy: float,
    history: list[dict],
    review_sources: dict[str, list[dict]],
    topic_stats: dict[str, dict],
) -> dict:
    batches = _build_review_batches(graded, topic_stats, review_sources, history)
    overall_context = _build_review_context(graded)
    review_provider, review_model = _resolve_review_channel()

    def call(batch: dict) -> dict:
        return llm_service.chat_json(
            prompts.REVIEW_SYSTEM,
            prompts.build_review_user(
                batch["items"],
                score,
                accuracy,
                batch["history"],
                _format_review_sources(batch["sources"]),
                overall_context,
            ),
            max_tokens=1600,
            timeout=REVIEW_TIMEOUT_SECONDS,
            provider=review_provider,
            model=review_model,
        )

    if len(batches) == 1:
        try:
            return call(batches[0])
        except llm_service.LlmError as exc:
            fallback_batches = _split_review_batches(batches[0])
            if len(fallback_batches) <= 1:
                local = _build_local_review(
                    graded, score, accuracy, review_sources, topic_stats
                )
                local["review_error"] = f"模型调用超时，已生成基础复习建议：{exc}"
                return local
            try:
                fallback_results = _run_review_calls(
                    fallback_batches,
                    score,
                    accuracy,
                    overall_context,
                    max_tokens=1200,
                    timeout=REVIEW_FALLBACK_TIMEOUT_SECONDS,
                    concurrency=REVIEW_FALLBACK_CONCURRENCY,
                )
                merged = _merge_reviews(fallback_results)
                merged["review_error"] = f"完整复习建议超时，已保留拆分后的部分建议：{exc}"
                local = _build_local_review(
                    graded, score, accuracy, review_sources, topic_stats
                )
                return _merge_local_coverage(merged, local)
            except llm_service.LlmError:
                local = _build_local_review(
                    graded, score, accuracy, review_sources, topic_stats
                )
                local["review_error"] = f"模型调用超时，已生成基础复习建议：{exc}"
                return local

    results: list[dict] = []
    errors: list[str] = []
    failed_batches: list[dict] = []

    with ThreadPoolExecutor(max_workers=REVIEW_MAX_CONCURRENCY) as executor:
        futures = {executor.submit(call, batch): index for index, batch in enumerate(batches)}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except llm_service.LlmError as exc:
                errors.append(str(exc))
                failed_batches.append(batches[futures[future]])

    if failed_batches:
        fallback_batches = [
            small_batch
            for batch in failed_batches
            for small_batch in _split_review_batches(batch)
        ]
        try:
            fallback_results = _run_review_calls(
                fallback_batches,
                score,
                accuracy,
                overall_context,
                max_tokens=1200,
                timeout=REVIEW_FALLBACK_TIMEOUT_SECONDS,
                concurrency=REVIEW_FALLBACK_CONCURRENCY,
            )
            results.extend(fallback_results)
            errors = []
        except llm_service.LlmError as fallback_exc:
            errors = [str(fallback_exc)]
            results.append(
                _build_local_review(graded, score, accuracy, review_sources, topic_stats)
            )

    if not results:
        raise llm_service.LlmError(errors[0] if errors else "复习建议生成失败。")

    merged = _merge_reviews(results)
    # 模型只深度分析优先级最高的弱题；如果本次弱题超过模型输入上限，
    # 把剩余题目合并进本地覆盖层，避免这些错题在复习指引里被遗漏。
    focus_count = len(_review_focus_items(graded, topic_stats))
    if focus_count > REVIEW_LLM_ITEM_LIMIT:
        coverage = _build_local_review(
            graded, score, accuracy, review_sources, topic_stats
        )
        coverage.pop("review_error", None)
        merged = _merge_local_coverage(merged, coverage)

    if errors:
        merged["review_error"] = (
            f"部分深度分析失败，已用基础复习建议覆盖剩余弱题：{errors[0]}"
        )
    return merged


def _split_review_batches(batch: dict) -> list[dict]:
    items = batch.get("items") or []
    return [
        {
            "items": items[offset : offset + REVIEW_FALLBACK_BATCH_SIZE],
            "history": batch.get("history") or [],
            "sources": batch.get("sources") or {},
        }
        for offset in range(0, len(items), REVIEW_FALLBACK_BATCH_SIZE)
    ]


def _build_local_review(
    graded: list[dict],
    score: float,
    accuracy: float,
    review_sources: dict[str, list[dict]],
    topic_stats: dict[str, dict],
) -> dict:
    """LLM 超时时用题目解析和来源片段生成基础复习建议。"""
    focus = _prioritise_review_items(
        _review_focus_items(graded, topic_stats),
        topic_stats,
    )
    by_topic: dict[str, list[dict]] = {}
    for item in focus:
        topic = str(item.get("topic") or "未分类").strip() or "未分类"
        by_topic.setdefault(topic, []).append(item)

    weak_topics: list[dict] = []
    for topic, items in by_topic.items():
        first = items[0]
        wrong_options = "、".join(first.get("user_answer") or [])
        correct_options = "、".join(first.get("correct_answer") or [])
        comparison = (
            f"示例题里你选了「{wrong_options}」，正确答案是「{correct_options}」。"
            if wrong_options
            else f"示例题未作答，正确答案是「{correct_options}」。"
        )
        explanation = str(first.get("explanation") or "").strip()
        diagnosis = (
            f"这个知识点在本次有 {len(items)} 道弱题。{comparison}"
            + (f"题目解析提到：{explanation}" if explanation else "先用这道题的答案差异回看解析。")
        )
        weak_topics.append(
            {
                "topic": topic,
                "diagnosis": diagnosis,
                "plain_summary": (
                    f"先把「{topic}」拆成：题目问了什么、正确答案为什么成立、你选的答案为什么不够完整。"
                ),
                "study_points": [
                    f"记住这组差异：你选「{wrong_options or '未作答'}」，正确是「{correct_options}」。",
                    explanation or "回到原文核对这道题对应的标准做法。",
                ],
            }
        )

    if focus:
        summary = (
            f"本次得分 {score} 分，正确率 {round(accuracy * 100)}%。"
            f"已用本地兜底覆盖 {len(focus)} 道弱题、{len(by_topic)} 个知识点。"
        )
    else:
        summary = f"本次得分 {score} 分，正确率 {round(accuracy * 100)}%，未发现明显弱题。"

    return {
        "summary": summary,
        "mastery_level": _local_mastery_level(accuracy),
        "can_advance": accuracy >= 0.85,
        "advance_reason": "本地兜底建议只能保证基础覆盖，达到标准后再进入下一板块。",
        "weak_topics": weak_topics,
        "encouragement": "成绩已经保存；先用基础建议补齐错题，模型恢复后再拿完整分析。",
    }


def _run_review_calls(
    batches: list[dict],
    score: float,
    accuracy: float,
    overall_context: str,
    *,
    max_tokens: int,
    timeout: float,
    concurrency: int,
) -> list[dict]:
    """并发执行复习批次；任一批次失败时整体抛错，由调用方决定是否保留部分结果。"""
    results: list[dict] = []
    review_provider, review_model = _resolve_review_channel()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = {
            executor.submit(
                llm_service.chat_json,
                prompts.REVIEW_SYSTEM,
                prompts.build_review_user(
                    batch["items"],
                    score,
                    accuracy,
                    batch.get("history") or [],
                    _format_review_sources(batch.get("sources") or {}),
                    overall_context,
                ),
                max_tokens=max_tokens,
                timeout=timeout,
                provider=review_provider,
                model=review_model,
            ): batch
            for batch in batches
        }
        for future in as_completed(futures):
            results.append(future.result())
    return results


def grade(paper_id: str, answers: dict[str, list[str]]) -> dict:
    paper = get_paper(paper_id, include_answers=True)
    questions = paper.get("questions") or []
    if not questions:
        raise QuizError("这份试卷没有题目。")

    graded: list[dict] = []
    correct_count = 0
    topic_catalog = _topic_catalog()

    for question in questions:
        qid = question["question_id"]
        picked = [
            str(key).strip().upper()
            for key in (answers.get(qid) or [])
            if str(key).strip()
        ]
        picked = sorted(set(picked))
        expected = sorted(question.get("answer") or [])
        is_correct = picked == expected and bool(picked)
        if is_correct:
            correct_count += 1
        graded.append(
            {
                "question_id": qid,
                "type": question["type"],
                "stem": question["stem"],
                "options": question["options"],
                "topic": _canonical_topic(question.get("topic") or "未分类", topic_catalog),
                "difficulty": question.get("difficulty", "medium"),
                "user_answer": picked,
                "correct_answer": expected,
                "is_correct": is_correct,
                "explanation": question.get("explanation", ""),
                "source_title": question.get("source_title", ""),
                "source_chunk_ids": question.get("source_chunk_ids", []),
                "user_answer_text": _answer_text(question, picked),
                "correct_answer_text": _answer_text(question, expected),
            }
        )

    total = len(questions)
    accuracy = correct_count / total if total else 0.0
    score = round(accuracy * 100, 1)

    topic_stats = _update_mastery(graded)
    _update_mistakes(graded, paper)
    review_service.sync_grade(graded, paper)
    review_sources = _collect_review_sources(graded, topic_stats, paper)

    history = [
        {
            "topic": topic,
            "total": stat["total"],
            "correct": stat["correct"],
            "accuracy": stat["correct"] / stat["total"] if stat["total"] else 0.0,
        }
        for topic, stat in topic_stats.items()
    ]

    try:
        review = _generate_review(graded, score, accuracy, history, review_sources, topic_stats)
    except llm_service.LlmError as exc:
        # 判分是本地算的；模型不可用时复习指引降级为基础建议，成绩不丢。
        review = _build_local_review(graded, score, accuracy, review_sources, topic_stats)
        review["review_error"] = f"模型调用超时，已生成基础复习建议：{exc}"

    review = _normalise_review(review, accuracy, review_sources)
    review["learning_guide"] = _build_learning_guide(graded, paper)

    attempt_id = uuid.uuid4().hex
    attempt = {
        "attempt_id": attempt_id,
        "paper_id": paper_id,
        "paper_title": paper.get("title", ""),
        "score": score,
        "accuracy": accuracy,
        "correct_count": correct_count,
        "total": total,
        "questions": graded,
        "review": review,
        "created_at": int(time.time()),
    }
    _write_json(ATTEMPTS_DIR / f"{attempt_id}.json", attempt)
    return attempt


def _local_mastery_level(accuracy: float) -> str:
    if accuracy >= 0.9:
        return "mastered"
    if accuracy >= 0.8:
        return "proficient"
    if accuracy >= 0.6:
        return "developing"
    return "beginner"


def _normalise_review(
    review: dict,
    accuracy: float,
    review_sources: dict[str, list[dict]] | None = None,
) -> dict:
    level = str(review.get("mastery_level") or "").strip().lower()
    if level not in ("beginner", "developing", "proficient", "mastered"):
        level = _local_mastery_level(accuracy)

    weak_topics = []
    for item in review.get("weak_topics") or []:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()
        if not topic:
            continue
        source_refs = (review_sources or {}).get(topic) or []
        source_status = str(item.get("source_status") or "").strip().lower()
        if source_status not in ("reinforce", "missing"):
            source_status = "reinforce" if source_refs else "missing"
        if not source_refs:
            source_status = "missing"
        weak_topics.append(
            {
                "topic": topic,
                "diagnosis": str(item.get("diagnosis") or "").strip(),
                "plain_summary": str(item.get("plain_summary") or "").strip(),
                "analogy": str(item.get("analogy") or "").strip(),
                "flow_steps": [
                    str(v).strip() for v in (item.get("flow_steps") or []) if str(v).strip()
                ],
                "source_status": source_status,
                "source_note": str(item.get("source_note") or "").strip(),
                "study_points": [
                    str(v).strip() for v in (item.get("study_points") or []) if str(v).strip()
                ],
                "next_actions": [
                    str(v).strip() for v in (item.get("next_actions") or []) if str(v).strip()
                ],
                "source_refs": source_refs,
            }
        )

    study_plan = _normalise_review_steps(
        review.get("study_plan") or [],
        review_sources,
    )

    # 正确率不达标时不允许放行，避免模型过于宽松。
    can_advance = bool(review.get("can_advance")) and accuracy >= 0.85

    return {
        "summary": str(review.get("summary") or "").strip(),
        "mastery_level": level,
        "can_advance": can_advance,
        "advance_reason": str(review.get("advance_reason") or "").strip(),
        "weak_topics": weak_topics,
        "study_plan": study_plan,
        "encouragement": str(review.get("encouragement") or "").strip(),
        "review_error": str(review.get("review_error") or "").strip(),
    }


def _normalise_review_steps(
    steps: list,
    review_sources: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """兼容旧版字符串步骤，并为新版步骤按知识点补齐原文依据。"""
    normalised: list[dict] = []
    for raw in steps:
        if isinstance(raw, str):
            item = {"action": raw.strip()}
        elif isinstance(raw, dict):
            item = raw
        else:
            continue

        action = str(
            item.get("action")
            or item.get("step")
            or item.get("description")
            or ""
        ).strip()
        if not action:
            continue

        topic = str(item.get("topic") or "").strip()
        source_refs = (review_sources or {}).get(topic) or [] if topic else []
        source_status = str(item.get("source_status") or "").strip().lower()
        if source_status not in ("reinforce", "missing"):
            source_status = "reinforce" if source_refs else ""
        if topic and not source_refs:
            source_status = "missing"

        normalised.append(
            {
                "topic": topic,
                "action": action,
                "source_status": source_status,
                "source_note": str(item.get("source_note") or "").strip(),
                "source_refs": source_refs,
            }
        )
    return normalised[:8]


# --------------------------------------------------------------------------
# 掌握度与错题本
# --------------------------------------------------------------------------


def _update_mastery(graded: list[dict]) -> dict:
    mastery = _ensure_mastery_schema()
    touched: dict = {}
    now = int(time.time())
    for item in graded:
        topic = item.get("topic") or "未分类"
        stat = _normalise_topic_stat(mastery.get(topic), now)
        stat["total"] += 1
        qtype = item.get("type") if item.get("type") in VALID_TYPES else ""
        if qtype:
            stat["by_type"][qtype]["total"] += 1
        if item["is_correct"]:
            stat["correct"] += 1
            stat["streak"] = stat.get("streak", 0) + 1
            if qtype:
                stat["by_type"][qtype]["correct"] += 1
        else:
            stat["streak"] = 0
        stat["updated_at"] = now
        mastery[topic] = stat
        touched[topic] = stat
    _write_json(MASTERY_FILE, mastery)
    return touched


def _update_mistakes(graded: list[dict], paper: dict) -> None:
    mistakes = list_mistakes(limit=10_000)
    by_key = {item["key"]: item for item in mistakes if isinstance(item, dict) and "key" in item}
    now = int(time.time())

    paper_docs = _mistake_paper_docs(paper)

    for item in graded:
        key = item["stem"][:120]
        existing = by_key.get(key)
        source_key = _title_key(item.get("source_title") or "")
        source_doc_id = ""
        for doc in paper_docs:
            if doc.get("doc_id") and _title_key(doc.get("title") or "") == source_key:
                source_doc_id = doc["doc_id"]
                break
        lineage = {
            "paper_id": str(paper.get("paper_id") or ""),
            "source_doc_id": source_doc_id,
            "source_docs": (
                [{"doc_id": source_doc_id, "title": item.get("source_title", "")}]
                if source_doc_id
                else []
            ),
            "source_chunk_ids": list(item.get("source_chunk_ids") or []),
            "paper_doc_ids": [doc["doc_id"] for doc in paper_docs if doc.get("doc_id")],
            "paper_doc_titles": [doc["title"] for doc in paper_docs if doc.get("title")],
        }
        if item["is_correct"]:
            if existing:
                existing["correct_streak"] = existing.get("correct_streak", 0) + 1
                existing["last_seen_at"] = now
                if existing["correct_streak"] >= MISTAKE_CLEAR_STREAK:
                    by_key.pop(key, None)
            continue

        if existing:
            existing["wrong_count"] = existing.get("wrong_count", 0) + 1
            existing["correct_streak"] = 0
            existing["last_seen_at"] = now
            existing["user_answer_text"] = item["user_answer_text"]
            for field, value in lineage.items():
                if not existing.get(field):
                    existing[field] = value
            continue

        by_key[key] = {
            "key": key,
            "topic": item.get("topic") or "未分类",
            "type": item["type"],
            "stem": item["stem"],
            "options": item["options"],
            "correct_answer": item["correct_answer"],
            "correct_answer_text": item["correct_answer_text"],
            "user_answer_text": item["user_answer_text"],
            "explanation": item.get("explanation", ""),
            "source_title": item.get("source_title", ""),
            "paper_title": paper.get("title", ""),
            **lineage,
            "wrong_count": 1,
            "correct_streak": 0,
            "created_at": now,
            "last_seen_at": now,
        }

    ordered = sorted(
        by_key.values(),
        key=lambda item: (item.get("wrong_count", 0), item.get("last_seen_at", 0)),
        reverse=True,
    )
    _write_json(MISTAKES_FILE, ordered)


def _mistake_paper_docs(paper: dict | None) -> list[dict]:
    """从试卷中整理引用文档线索，兼容只有 doc_titles 的老试卷。"""
    if not isinstance(paper, dict):
        return []
    paper_docs = [
        {"doc_id": str(doc.get("doc_id") or ""), "title": str(doc.get("title") or "")}
        for doc in paper.get("source_docs") or []
        if isinstance(doc, dict) and (doc.get("doc_id") or doc.get("title"))
    ]
    if paper_docs:
        return paper_docs
    doc_titles = [str(title) for title in paper.get("doc_titles") or [] if title]
    doc_ids = [str(doc_id) for doc_id in paper.get("doc_ids") or []]
    return [
        {"doc_id": doc_ids[index] if index < len(doc_ids) else "", "title": title}
        for index, title in enumerate(doc_titles)
    ]


def _knowledge_docs_by_title() -> dict[str, dict]:
    docs_by_title: dict[str, dict] = {}
    try:
        for doc in knowledge_service.list_documents():
            docs_by_title[_title_key(doc.get("title") or "")] = doc
    except Exception:
        pass
    return docs_by_title


def _papers_by_title() -> dict[str, dict]:
    papers_by_title: dict[str, dict] = {}
    if PAPERS_DIR.exists():
        for path in PAPERS_DIR.glob("*.json"):
            paper = _read_json(path, None)
            if isinstance(paper, dict) and paper.get("title"):
                papers_by_title.setdefault(_title_key(str(paper["title"])), paper)
    return papers_by_title


def _backfill_mistake_lineage(
    item: dict,
    docs_by_title: dict[str, dict],
    papers_by_title: dict[str, dict],
) -> None:
    """为历史错题补充来源文档线索；只新增字段，不改动既有数据。"""
    item["lineage_backfilled"] = True
    paper = papers_by_title.get(_title_key(item.get("paper_title") or ""))
    paper_docs = _mistake_paper_docs(paper)

    if not item.get("paper_id") and isinstance(paper, dict) and paper.get("paper_id"):
        item["paper_id"] = str(paper["paper_id"])
    if not item.get("paper_doc_ids"):
        ids = [doc["doc_id"] for doc in paper_docs if doc.get("doc_id")]
        if ids:
            item["paper_doc_ids"] = ids
    if not item.get("paper_doc_titles"):
        titles = [doc["title"] for doc in paper_docs if doc.get("title")]
        if titles:
            item["paper_doc_titles"] = titles

    source_key = _title_key(item.get("source_title") or "")
    source_doc = docs_by_title.get(source_key) if source_key else None
    if not item.get("source_doc_id") and source_doc:
        item["source_doc_id"] = str(source_doc.get("doc_id") or "")
    if not item.get("source_doc_id"):
        for doc in paper_docs:
            if doc.get("doc_id") and _title_key(doc.get("title") or "") == source_key:
                item["source_doc_id"] = doc["doc_id"]
                break

    if not item.get("source_docs"):
        doc_id = str(item.get("source_doc_id") or "")
        if doc_id:
            item["source_docs"] = [
                {
                    "doc_id": doc_id,
                    "title": str(
                        (source_doc or {}).get("title")
                        or item.get("source_title")
                        or ""
                    ),
                }
            ]
        elif paper_docs:
            # 老数据无法精确定位来源时，退化为原试卷引用过的全部文档。
            item["source_docs"] = list(paper_docs)


def _load_mistakes() -> list[dict]:
    """读取错题列表，并为缺少来源线索的老数据按需补齐（只加字段）。"""
    mistakes = _read_json(MISTAKES_FILE, [])
    if not isinstance(mistakes, list) or not mistakes:
        return mistakes if isinstance(mistakes, list) else []
    if all(
        isinstance(item, dict) and item.get("lineage_backfilled")
        for item in mistakes
    ):
        return mistakes

    docs_by_title = _knowledge_docs_by_title()
    papers_by_title = _papers_by_title()
    for item in mistakes:
        if isinstance(item, dict):
            _backfill_mistake_lineage(item, docs_by_title, papers_by_title)
    return mistakes


def list_mistakes(limit: int = 100) -> list[dict]:
    catalog = _topic_catalog()
    normalised = []
    for item in _load_mistakes():
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        copied["topic"] = _canonical_topic(str(copied.get("topic") or "未分类"), catalog)
        if "explanation" in copied:
            copied["explanation"] = _clean_explanation(copied.get("explanation"))
        normalised.append(copied)
    return normalised[:limit]


def delete_mistake(key: str) -> None:
    mistakes = list_mistakes(limit=10_000)
    remaining = [item for item in mistakes if item.get("key") != key]
    if len(remaining) == len(mistakes):
        raise QuizError("错题不存在。")
    _write_json(MISTAKES_FILE, remaining)
    review_service.archive_by_question_key(key)


def list_weak_topics(limit: int = 10, threshold: float = 0.8) -> list[dict]:
    mastery = _ensure_mastery_schema()
    items: list[dict] = []
    for topic, stat in mastery.items():
        total = stat.get("total", 0)
        if not total:
            continue
        accuracy = stat.get("correct", 0) / total
        if accuracy < threshold:
            items.append(
                {
                    "topic": topic,
                    "total": total,
                    "correct": stat.get("correct", 0),
                    "accuracy": round(accuracy, 3),
                    "streak": stat.get("streak", 0),
                }
            )
    items.sort(key=lambda item: (item["accuracy"], -item["total"]))
    return items[:limit]


def get_progress() -> dict:
    mastery = _ensure_mastery_schema()
    catalog = _topic_catalog()
    topics = [
        _topic_entry(topic, stat, _topic_module(topic, catalog))
        for topic, stat in mastery.items()
        if stat.get("total", 0)
    ]
    topics.sort(key=lambda item: item["accuracy"])

    attempts = list_attempts(limit=10)
    answered = sum(item["total"] for item in topics)
    correct = sum(item["correct"] for item in topics)
    guide = _build_learning_guide()
    return {
        "topics": topics,
        "recent_attempts": attempts,
        "mistake_count": len(list_mistakes(limit=10_000)),
        "answered_total": answered,
        "correct_total": correct,
        "overall_accuracy": _accuracy(correct, answered),
        "type_stats": guide["type_stats"],
        "module_stats": guide["module_stats"],
        "mastered_topics": guide["mastered_topics"],
        "focus_topics": guide["focus_topics"],
        "next_paper": guide["next_paper"],
        "interview_ready": guide["interview_ready"],
        "interview_reasons": guide["interview_reasons"],
        "guide_summary": guide["summary"],
        "scope_note": guide["scope_note"],
    }


def list_attempts(limit: int = 20) -> list[dict]:
    ensure_dirs()
    items: list[dict] = []
    for path in ATTEMPTS_DIR.glob("*.json"):
        attempt = _read_json(path, None)
        if not attempt:
            continue
        items.append(
            {
                "attempt_id": attempt["attempt_id"],
                "paper_id": attempt.get("paper_id", ""),
                "paper_title": attempt.get("paper_title", ""),
                "score": attempt.get("score", 0),
                "correct_count": attempt.get("correct_count", 0),
                "total": attempt.get("total", 0),
                "created_at": attempt.get("created_at", 0),
            }
        )
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items[:limit]


def get_attempt(attempt_id: str) -> dict:
    attempt = _read_json(ATTEMPTS_DIR / f"{attempt_id}.json", None)
    if not attempt:
        raise QuizError("答卷不存在。")
    catalog = _topic_catalog()
    copied = dict(attempt)
    copied["questions"] = []
    for question in attempt.get("questions") or []:
        question_copy = dict(question)
        question_copy["topic"] = _canonical_topic(
            str(question_copy.get("topic") or "未分类"),
            catalog,
        )
        question_copy["explanation"] = _clean_explanation(
            question_copy.get("explanation")
        )
        copied["questions"].append(question_copy)
    review = dict(copied.get("review") or {})
    sources = {
        str(item.get("topic") or "").strip(): list(item.get("source_refs") or [])
        for item in review.get("weak_topics") or []
        if isinstance(item, dict) and str(item.get("topic") or "").strip()
    }
    if "study_plan" in review:
        review["study_plan"] = _normalise_review_steps(review.get("study_plan") or [], sources)
        copied["review"] = review
    return copied
