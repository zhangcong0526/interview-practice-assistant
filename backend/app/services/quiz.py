"""在线考题：关键词提炼、组卷、判分、错题本与掌握度追踪。

判分完全在本地完成，不依赖 LLM，保证成绩可复现；LLM 只负责命题和复习指引。
"""

import json
import time
import uuid
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
TOPICS_FILE = QUIZ_DIR / "topics.json"

VALID_TYPES = ("single", "multiple", "judge")
MAX_MATERIAL_CHARS = 24_000
MAX_QUESTIONS = 30
# 连续两次答对即视为该知识点已回稳，可以移出错题本。
MISTAKE_CLEAR_STREAK = 2


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


def _normalise_question(item: dict, index: int) -> dict | None:
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
        "topic": str(item.get("topic") or "").strip() or "未分类",
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

    weak_topics = [item["topic"] for item in list_weak_topics(limit=8)] if focus_weak else []
    avoid_stems = _recent_stems(limit=40)

    try:
        raw = llm_service.chat_json(
            prompts.QUIZ_SYSTEM,
            prompts.build_quiz_user(
                material, keywords, counts, difficulty, avoid_stems, weak_topics
            ),
        )
    except llm_service.LlmError as exc:
        raise QuizError(f"组卷失败：{exc}") from exc

    questions: list[dict] = []
    for item in raw.get("questions") or []:
        if not isinstance(item, dict):
            continue
        normalised = _normalise_question(item, len(questions) + 1)
        if normalised:
            questions.append(normalised)

    if not questions:
        raise QuizError("模型没有生成可用的题目，请重试或调整关键词。")

    paper_id = uuid.uuid4().hex
    title = str(raw.get("title") or "").strip()
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


def generate_mistake_paper(limit: int = 8, difficulty: str = "mixed") -> dict:
    """用错题本里的知识点重新组卷。"""
    mistakes = list_mistakes()
    if not mistakes:
        raise QuizError("错题本是空的，先去做一份测验吧。")
    topics: list[str] = []
    for item in mistakes:
        topic = item.get("topic")
        if topic and topic not in topics:
            topics.append(topic)
        if len(topics) >= limit:
            break
    return generate_paper(
        topics,
        [],
        single=max(2, min(5, len(topics))),
        multiple=2,
        judge=2,
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


def _recent_stems(limit: int = 40) -> list[str]:
    stems: list[str] = []
    for paper in list_papers(limit=6):
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


# --------------------------------------------------------------------------
# 判分
# --------------------------------------------------------------------------


def _answer_text(question: dict, keys: list[str]) -> str:
    lookup = {option["key"]: option["text"] for option in question.get("options") or []}
    return "；".join(f"{key}. {lookup.get(key, '')}" for key in keys) if keys else ""


def grade(paper_id: str, answers: dict[str, list[str]]) -> dict:
    paper = get_paper(paper_id, include_answers=True)
    questions = paper.get("questions") or []
    if not questions:
        raise QuizError("这份试卷没有题目。")

    graded: list[dict] = []
    correct_count = 0

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
                "topic": question.get("topic") or "未分类",
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
    mastery = _read_json(MASTERY_FILE, {})
    touched: dict = {}
    now = int(time.time())
    for item in graded:
        topic = item.get("topic") or "未分类"
        stat = mastery.get(topic) or {"total": 0, "correct": 0, "streak": 0, "updated_at": now}
        stat["total"] += 1
        if item["is_correct"]:
            stat["correct"] += 1
            stat["streak"] = stat.get("streak", 0) + 1
        else:
            stat["streak"] = 0
        stat["updated_at"] = now
        mastery[topic] = stat
        touched[topic] = stat
    _write_json(MASTERY_FILE, mastery)
    return touched


def _update_mistakes(graded: list[dict], paper: dict) -> None:
    mistakes = _read_json(MISTAKES_FILE, [])
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
    return mistakes[:limit] if isinstance(mistakes, list) else []


def delete_mistake(key: str) -> None:
    mistakes = list_mistakes(limit=10_000)
    remaining = [item for item in mistakes if item.get("key") != key]
    if len(remaining) == len(mistakes):
        raise QuizError("错题不存在。")
    _write_json(MISTAKES_FILE, remaining)


def list_weak_topics(limit: int = 10, threshold: float = 0.8) -> list[dict]:
    mastery = _read_json(MASTERY_FILE, {})
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
    mastery = _read_json(MASTERY_FILE, {})
    topics: list[dict] = []
    for topic, stat in mastery.items():
        total = stat.get("total", 0)
        if not total:
            continue
        accuracy = stat.get("correct", 0) / total
        topics.append(
            {
                "topic": topic,
                "total": total,
                "correct": stat.get("correct", 0),
                "accuracy": round(accuracy, 3),
                "streak": stat.get("streak", 0),
                "level": _local_mastery_level(accuracy),
            }
        )
    topics.sort(key=lambda item: item["accuracy"])

    attempts = list_attempts(limit=10)
    answered = sum(item["total"] for item in topics)
    correct = sum(item["correct"] for item in topics)
    return {
        "topics": topics,
        "recent_attempts": attempts,
        "mistake_count": len(list_mistakes(limit=10_000)),
        "answered_total": answered,
        "correct_total": correct,
        "overall_accuracy": round(correct / answered, 3) if answered else 0.0,
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
    return attempt
