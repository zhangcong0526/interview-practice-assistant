"""在线考题：关键词提炼、组卷、判分、错题本与掌握度追踪。

判分完全在本地完成，不依赖 LLM，保证成绩可复现；LLM 只负责命题和复习指引。
"""

import json
import re
import shutil
import time
import uuid
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..config import settings
from .. import prompts
from . import knowledge as knowledge_service
from . import llm as llm_service


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
MAX_QUESTIONS = 30
QUIZ_LLM_BATCH_SIZE = 8
QUIZ_FAST_SINGLE_CALL_LIMIT = 20
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


def _collect_material(keywords: list[str], doc_ids: list[str], per_keyword: int = 4) -> tuple[str, list[str]]:
    """按关键词检索知识库片段，拼成命题资料。

    没有关键词时退化为使用指定文档（或全部文档）的开头内容。
    """
    used_titles: list[str] = []
    blocks: list[str] = []
    seen_chunks: set[str] = set()
    budget = MAX_MATERIAL_CHARS

    def push(title: str, text: str, chunk_id: str) -> None:
        nonlocal budget
        if chunk_id in seen_chunks or budget <= 0:
            return
        seen_chunks.add(chunk_id)
        snippet = text[:budget]
        blocks.append(f"[资料来源：{title}]\n{snippet}")
        budget -= len(snippet)
        if title not in used_titles:
            used_titles.append(title)

    doc_filter = set(doc_ids or [])

    for keyword in keywords:
        for hit in knowledge_service.search(keyword, per_keyword):
            if doc_filter and hit["doc_id"] not in doc_filter:
                continue
            push(hit["title"], hit["text"], hit["chunk_id"])

    if not blocks:
        for doc in knowledge_service.list_documents():
            if doc_filter and doc["doc_id"] not in doc_filter:
                continue
            full = _load_document_text(doc["doc_id"])
            if full:
                push(doc["title"], full, doc["doc_id"])

    return "\n\n".join(blocks), used_titles


def _load_document_text(doc_id: str) -> str:
    path = knowledge_service.DOCS_DIR / f"{doc_id}.json"
    doc = _read_json(path, None)
    if not doc:
        return ""
    return "\n\n".join(doc.get("chunks") or [])


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

    material_parts: list[str] = []
    budget = MAX_MATERIAL_CHARS
    for doc in documents:
        if budget <= 0:
            break
        text = _load_document_text(doc["doc_id"])[:budget]
        if text:
            material_parts.append(f"[资料来源：{doc['title']}]\n{text}")
            budget -= len(text)
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
        for qtype in VALID_TYPES:
            take = min(remaining[qtype], QUIZ_LLM_BATCH_SIZE - batch_total)
            batch[qtype] = take
            batch_total += take
            remaining[qtype] -= take
            if batch_total >= QUIZ_LLM_BATCH_SIZE:
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


def _normalise_question(item: dict, index: int, topic_catalog: dict | None = None) -> dict | None:
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

    return {
        "question_id": f"q{index}",
        "type": qtype,
        "stem": stem,
        "options": options,
        "answer": answer,
        "explanation": str(item.get("explanation") or "").strip(),
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

    material, used_titles = _collect_material(keywords, doc_ids or [])
    if not material.strip():
        raise QuizError("没有检索到相关资料，请更换关键词或先导入知识库文档。")

    topic_catalog = _topic_catalog()
    keywords = [_canonical_topic(keyword, topic_catalog) for keyword in keywords]
    weak_topics = [item["topic"] for item in list_weak_topics(limit=8)] if focus_weak else []
    coverage_hint = _topic_type_coverage_hint(keywords)
    recent_stems = _recent_stems(limit=80)
    raw_title = ""

    def normalise_raw(raw: dict) -> list[dict]:
        nonlocal raw_title
        raw_title = str(raw.get("title") or raw_title or "").strip()
        normalised: list[dict] = []
        for item in raw.get("questions") or []:
            if isinstance(item, dict):
                question = _normalise_question(item, len(normalised) + 1, topic_catalog)
                if question:
                    normalised.append(question)
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

    if any(deficits.values()) or len(selected) != total:
        if retry_error is not None:
            raise QuizError(f"补充命题失败：{retry_error}") from retry_error
        raise QuizError("模型返回的有效题目不足，请稍后重试或更换关键词。")

    questions = []
    type_order = {qtype: index for index, qtype in enumerate(VALID_TYPES)}
    selected.sort(key=lambda item: type_order[item["type"]])
    for index, question in enumerate(selected, start=1):
        question["question_id"] = f"q{index}"
        questions.append(question)

    paper_id = uuid.uuid4().hex
    title = raw_title
    if not title:
        title = ("、".join(keywords[:3]) or "综合练习") + " 测验"

    paper = {
        "paper_id": paper_id,
        "title": title,
        "keywords": keywords,
        "doc_titles": used_titles,
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


def generate_mistake_paper(
    limit: int = 8,
    difficulty: str = "mixed",
    scope: str = "current",
    attempt_id: str = "",
) -> dict:
    """按本次错题或累计错题的知识点重新生成变形题。"""
    if scope == "current":
        topics = _current_mistake_topics(attempt_id, limit)
    else:
        topics = _all_mistake_topics(limit)
    counts = {
        "single": max(2, min(5, len(topics))),
        "multiple": 2,
        "judge": 2,
    }
    total = sum(counts.values())
    if total > MAX_QUESTIONS:
        counts["judge"] = max(0, MAX_QUESTIONS - counts["single"] - counts["multiple"])
    return generate_paper(
        topics,
        [],
        single=counts["single"],
        multiple=counts["multiple"],
        judge=counts["judge"],
        difficulty=difficulty,
        focus_weak=True,
    )


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


def _accuracy(correct: int, total: int) -> float:
    return round(correct / total, 3) if total else 0.0


def _correct_types(stat: dict) -> list[str]:
    return [
        qtype
        for qtype in VALID_TYPES
        if stat.get("by_type", {}).get(qtype, {}).get("correct", 0) > 0
    ]


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
                "user_answer_text": _answer_text(question, picked),
                "correct_answer_text": _answer_text(question, expected),
            }
        )

    total = len(questions)
    accuracy = correct_count / total if total else 0.0
    score = round(accuracy * 100, 1)

    topic_stats = _update_mastery(graded)
    _update_mistakes(graded, paper)

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
        review = llm_service.chat_json(
            prompts.REVIEW_SYSTEM,
            prompts.build_review_user(graded, score, accuracy, history),
        )
    except llm_service.LlmError as exc:
        # 判分是本地算的，复习指引失败不应该让用户丢失成绩。
        review = {
            "summary": f"本次得分 {score} 分，正确率 {round(accuracy * 100)}%。",
            "mastery_level": _local_mastery_level(accuracy),
            "can_advance": accuracy >= 0.85,
            "advance_reason": "",
            "weak_topics": [],
            "study_plan": [],
            "encouragement": "",
            "review_error": f"复习建议生成失败：{exc}",
        }

    review = _normalise_review(review, accuracy)
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


def _normalise_review(review: dict, accuracy: float) -> dict:
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
        weak_topics.append(
            {
                "topic": topic,
                "diagnosis": str(item.get("diagnosis") or "").strip(),
                "study_points": [
                    str(v).strip() for v in (item.get("study_points") or []) if str(v).strip()
                ],
                "next_actions": [
                    str(v).strip() for v in (item.get("next_actions") or []) if str(v).strip()
                ],
            }
        )

    # 正确率不达标时不允许放行，避免模型过于宽松。
    can_advance = bool(review.get("can_advance")) and accuracy >= 0.85

    return {
        "summary": str(review.get("summary") or "").strip(),
        "mastery_level": level,
        "can_advance": can_advance,
        "advance_reason": str(review.get("advance_reason") or "").strip(),
        "weak_topics": weak_topics,
        "study_plan": [
            str(v).strip() for v in (review.get("study_plan") or []) if str(v).strip()
        ],
        "encouragement": str(review.get("encouragement") or "").strip(),
        "review_error": str(review.get("review_error") or "").strip(),
    }


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

    for item in graded:
        key = item["stem"][:120]
        existing = by_key.get(key)
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


def list_mistakes(limit: int = 100) -> list[dict]:
    mistakes = _read_json(MISTAKES_FILE, [])
    if not isinstance(mistakes, list):
        return []
    catalog = _topic_catalog()
    normalised = []
    for item in mistakes:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        copied["topic"] = _canonical_topic(str(copied.get("topic") or "未分类"), catalog)
        normalised.append(copied)
    return normalised[:limit]


def delete_mistake(key: str) -> None:
    mistakes = list_mistakes(limit=10_000)
    remaining = [item for item in mistakes if item.get("key") != key]
    if len(remaining) == len(mistakes):
        raise QuizError("错题不存在。")
    _write_json(MISTAKES_FILE, remaining)


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
        copied["questions"].append(question_copy)
    return copied
