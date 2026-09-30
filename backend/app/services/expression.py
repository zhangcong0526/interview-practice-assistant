"""表达训练专项：针对「有货说不出、紧张、口语化」的刻意练习。

和模拟面试的区别：这里一次只练一道题，评分只看表达方式，不考技术对错。
指标全部本地从转写文本计算，可复现；LLM 只负责给具体的表达教练建议。
每天的练习结果落盘，用趋势验证是否真的在进步，而不是凭感觉。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
import threading
import uuid
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from ..config import settings
from .. import prompts
from .. import roles as roles_service
from . import llm as llm_service
from . import question_bank
from . import knowledge as knowledge_service


class ExpressionError(RuntimeError):
    pass


SESSION_DIR = settings.data_dir / "expression" / "sessions"
KEYWORD_CACHE_DIR = settings.data_dir / "expression" / "keyword_cache"
KEYWORD_CACHE_VERSION = "speaking-keywords-v2"
SPEAKING_MATERIAL_CACHE_DIR = settings.data_dir / "expression" / "speaking_material_cache"
SPEAKING_MATERIAL_CACHE_VERSION = "speaking-materials-v1"
GENERATED_QUESTION_CACHE_DIR = settings.data_dir / "expression" / "generated_questions"
GENERATED_QUESTION_CACHE_VERSION = "generated-questions-v1"
GENERATED_QUESTION_TTL_SECONDS = 7 * 24 * 3600
_SPEAKING_JOBS_LOCK = threading.Lock()
_SPEAKING_JOBS: set[str] = set()
_SPEAKING_WORK_LIMIT = threading.Semaphore(2)

# 纯粹的语气词，出现即扣分。
FILLER_WORDS = ("嗯", "呃", "唉", "哦", "啊", "额")
# 口头禅式连接词，正常说话也会用，但密度高说明表达缺乏组织。
CRUTCH_WORDS = ("然后", "就是", "这个", "那个", "怎么说呢", "对吧", "是吧", "然后呢")
# 不确定、自我削弱的表达，密度高会让面试官觉得不笃定。
HEDGE_WORDS = ("我觉得", "可能", "大概", "好像", "应该是", "不知道", "怎么讲", "也许")
# 结构化表达信号。
STRUCTURE_WORDS = (
    "首先",
    "第一",
    "其次",
    "再者",
    "然后",
    "最后",
    "总结",
    "总的来说",
    "一方面",
    "另一方面",
)

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 连续口吃/重启：同一个词立刻重复，如「我我」「然后然后」「这个这个」。
RESTART_RE = re.compile(r"([\u4e00-\u9fff]{1,3})\1+")


def ensure_dirs() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    KEYWORD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SPEAKING_MATERIAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _keyword_norm(text: str) -> str:
    """归一化后做命中校验，保留路径、连字符等技术符号。"""
    return re.sub(
        r"[\s:：，,。；;、（）()「」『』【】\[\]\"'“”‘’]+",
        "",
        str(text).strip().lower(),
    )


def _clean_speaking_keyword(value: object) -> str:
    if not isinstance(value, str):
        return ""
    keyword = re.sub(r"^(?:[-*•·]|\d+[.、)])\s*", "", value.strip())
    keyword = re.sub(r"\s+", " ", keyword).strip("：:；;，,。.!！?？")
    return keyword


def _valid_speaking_keywords(keywords: object, question: str, answer: str) -> list[str]:
    if not isinstance(keywords, list):
        return []
    context = _keyword_norm(f"{question}\n{answer}")
    result: list[str] = []
    seen: set[str] = set()
    blocked = {"先给结论", "具体例子", "行动改变", "收尾观点"}
    blocked_norm = {_keyword_norm(item) for item in blocked}

    for raw in keywords:
        keyword = _clean_speaking_keyword(raw)
        normalized = _keyword_norm(keyword)
        if not keyword or len(keyword) > 24 or normalized in seen:
            continue
        if normalized in blocked_norm:
            continue
        if re.search(r"[。！？!?；;]", keyword):
            continue
        position = context.find(normalized)
        acceptable_position = False
        while position >= 0:
            before = context[position - 1] if position > 0 else ""
            after_position = position + len(normalized)
            after = context[after_position] if after_position < len(context) else ""
            cut_number = (
                (normalized[:1].isdigit() and before in {".", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"})
                or (normalized[-1:].isdigit() and after in {".", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"})
            )
            if not cut_number:
                acceptable_position = True
                break
            position = context.find(normalized, position + 1)
        if not acceptable_position:
            continue
        seen.add(normalized)
        result.append(keyword)

    return result[:8] if len(result) >= 5 else []


def _speaking_keywords(question: str, answer: str, fallback: list[str]) -> list[str]:
    """基于本题参考答案生成口播关键词；LLM 不可用时才使用离线兜底。"""
    if not answer.strip():
        return fallback

    digest = hashlib.sha1(f"{question}\n{answer}".encode("utf-8")).hexdigest()
    cache_path = KEYWORD_CACHE_DIR / f"{digest}.json"
    cached = _read_json(cache_path)
    if cached and cached.get("version") == KEYWORD_CACHE_VERSION and cached.get("hash") == digest:
        cached_keywords = _valid_speaking_keywords(cached.get("keywords"), question, answer)
        if cached_keywords:
            return cached_keywords

    try:
        raw = llm_service.chat_json(
            prompts.SPEAKING_KEYWORD_SYSTEM,
            prompts.build_expression_keyword_user(question, answer),
            max_tokens=1200,
        )
        keywords = _valid_speaking_keywords(raw.get("keywords"), question, answer)
        if keywords:
            ensure_dirs()
            payload = {
                "version": KEYWORD_CACHE_VERSION,
                "hash": digest,
                "keywords": keywords,
                "created_at": int(time.time()),
            }
            tmp_path = cache_path.with_name(cache_path.name + ".tmp")
            _write_json(tmp_path, payload)
            os.replace(tmp_path, cache_path)
            return keywords
    except Exception:
        # 关键词只是训练提示，不能因为 LLM 或缓存异常阻断出题。
        pass
    return fallback


def _clean_speaking_point(value: object) -> str:
    if not isinstance(value, str):
        return ""
    point = re.sub(r"^(?:[-*•·]|\d+[.、)])\s*", "", value.strip())
    point = re.sub(r"\s+", " ", point).strip("：:；;，,。.!！?？")
    return point


def _fallback_key_points(answer: str) -> list[str]:
    """LLM 不可用时，从题库答案本身抽短句兜底，不额外发明内容。"""
    normalized = re.sub(r"^#{1,6}\s*", "", answer.strip(), flags=re.MULTILINE)
    candidates = re.split(r"[。；;！!？?\n]+", normalized)
    result: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        point = _clean_speaking_point(raw)
        normalized_point = _keyword_norm(point)
        if not point or len(point) > 48 or normalized_point in seen:
            continue
        # 太短的主谓短语会让“标准要点”变成关键词，降低复盘意义。
        if len(point) < 8 and not re.search(r"[A-Za-z]", point):
            continue
        seen.add(normalized_point)
        result.append(point)
        if len(result) >= 5:
            break
    return result


def _fallback_reference_script(answer: str) -> str:
    """只截断和轻量清理题库答案，不凭空补写口播话术。"""
    text = re.sub(r"^(?:[-*•·]|\d+[.、)])\s*", "", answer.strip(), flags=re.MULTILINE)
    text = re.sub(r"\n{2,}", "\n", text)
    if len(text) <= 320:
        return text
    cut = text[:320]
    last_break = max(cut.rfind("。"), cut.rfind("；"), cut.rfind("；"), cut.rfind("\n"))
    return cut[:last_break + 1].strip() if last_break >= 80 else cut.strip()


def _valid_speaking_material(
    raw: object,
    question: str,
    answer: str,
) -> tuple[list[str], list[str], str] | None:
    if not isinstance(raw, dict):
        return None

    keywords = _valid_speaking_keywords(raw.get("keywords"), question, answer)
    points: list[str] = []
    point_seen: set[str] = set()
    for value in raw.get("key_points") or []:
        point = _clean_speaking_point(value)
        normalized = _keyword_norm(point)
        if not point or len(point) > 60 or normalized in point_seen:
            continue
        point_seen.add(normalized)
        points.append(point)

    script = str(raw.get("reference_script") or "").strip()
    script_chars = len(re.sub(r"\s+", "", script))
    if len(keywords) < 5 or not (3 <= len(points) <= 5) or not (140 <= script_chars <= 400):
        return None
    return keywords[:8], points[:5], script


def _prefetch_speaking_materials(
    question: str,
    answer: str,
    fallback_keywords: list[str],
) -> None:
    """后台补齐表达素材，避免首次取题被慢模型同步阻塞。"""
    digest = hashlib.sha1(f"material:{question}\n{answer}".encode("utf-8")).hexdigest()
    with _SPEAKING_JOBS_LOCK:
        if digest in _SPEAKING_JOBS:
            return
        _SPEAKING_JOBS.add(digest)

    def worker() -> None:
        try:
            with _SPEAKING_WORK_LIMIT:
                _speaking_materials(question, answer, fallback_keywords)
        finally:
            with _SPEAKING_JOBS_LOCK:
                _SPEAKING_JOBS.discard(digest)

    threading.Thread(
        target=worker,
        name=f"speaking-material-{digest[:10]}",
        daemon=True,
    ).start()


def _speaking_materials(
    question: str,
    answer: str,
    fallback_keywords: list[str],
    *,
    allow_llm: bool = True,
) -> tuple[list[str], list[str], str]:
    """一次生成并缓存表达素材，避免同一题反复调用 LLM。"""
    if not answer.strip():
        return fallback_keywords, [], ""

    digest = hashlib.sha1(f"material:{question}\n{answer}".encode("utf-8")).hexdigest()
    cache_path = SPEAKING_MATERIAL_CACHE_DIR / f"{digest}.json"
    cached = _read_json(cache_path)
    if cached and cached.get("version") == SPEAKING_MATERIAL_CACHE_VERSION and cached.get("hash") == digest:
        keywords = _valid_speaking_keywords(cached.get("keywords"), question, answer)
        cached_points = [
            point
            for point in (
                _clean_speaking_point(value)
                for value in (cached.get("key_points") if isinstance(cached.get("key_points"), list) else [])
            )
            if point
        ]
        script = str(cached.get("reference_script") or "").strip()
        if keywords and cached_points and script:
            return keywords, cached_points, script

    fallback = (
        _valid_speaking_keywords(fallback_keywords, question, answer) or fallback_keywords,
        _fallback_key_points(answer),
        _fallback_reference_script(answer),
    )
    if not allow_llm:
        _prefetch_speaking_materials(question, answer, fallback_keywords)
        return fallback

    try:
        raw = llm_service.chat_json(
            prompts.SPEAKING_MATERIAL_SYSTEM,
            prompts.build_speaking_material_user(question, answer),
            max_tokens=1400,
        )
        material = _valid_speaking_material(raw, question, answer)
        if material:
            keywords, points, script = material
            ensure_dirs()
            payload = {
                "version": SPEAKING_MATERIAL_CACHE_VERSION,
                "hash": digest,
                "keywords": keywords,
                "key_points": points,
                "reference_script": script,
                "created_at": int(time.time()),
            }
            tmp_path = cache_path.with_name(cache_path.name + ".tmp")
            _write_json(tmp_path, payload)
            os.replace(tmp_path, cache_path)
            return keywords, points, script
    except Exception:
        # 表达素材可以降级；提交后的客观指标不依赖它。
        pass

    return (
        _valid_speaking_keywords(fallback_keywords, question, answer) or fallback_keywords,
        _fallback_key_points(answer),
        _fallback_reference_script(answer),
    )


def _drill_display_question(item: dict) -> str:
    """练习题面和关键词缓存共用同一份清洗结果，避免批注导致缓存键不一致。"""
    question = re.sub(
        r"[（(][^（）()]{0,30}?(?:answers?\.md|第\d+题|改进版|已答|见文档|拓展题|追问)[^（）()]*[）)]\s*$",
        "",
        item["question"],
    ).strip()
    return question or item["question"]


def _topic_terms(topic_hint: str) -> set[str]:
    normalized = _keyword_norm(topic_hint)
    terms = {normalized} if normalized else set()
    parts = [part for part in re.split(r"[\s/、，,;；|+]+", topic_hint) if part]
    terms.update(part for part in (_keyword_norm(part) for part in parts) if len(part) >= 2)
    return terms


def _topic_matches(item: dict, terms: set[str]) -> bool:
    if not terms:
        return False
    haystack = _keyword_norm(
        " ".join(
            [
                str(item.get("question") or ""),
                str(item.get("section") or ""),
                str(item.get("intent") or ""),
                str(item.get("source") or ""),
                " ".join(item.get("keywords") or []),
                str(item.get("reference_answer") or "")[:1200],
            ]
        )
    )
    strong_terms = [term for term in terms if len(term) >= 3]
    if strong_terms and any(term in haystack for term in strong_terms):
        return True
    full = _keyword_norm(" ".join(sorted(terms)))
    return bool(full and full in haystack)
    return any(term in haystack for term in terms)


def _topic_match_score(item: dict, terms: set[str]) -> int:
    """为命中真题打分，让更贴近薄弱知识点的题排在前面。"""
    if not terms:
        return 0
    haystack = _keyword_norm(
        " ".join(
            [
                str(item.get("question") or ""),
                str(item.get("section") or ""),
                str(item.get("intent") or ""),
                str(item.get("source") or ""),
                " ".join(item.get("keywords") or []),
            ]
        )
    )
    score = sum(2 for term in terms if term and term in haystack)
    label = _keyword_norm(str(item.get("label") or ""))
    if label and any(term in label for term in terms if term):
        score += 3
    full = _keyword_norm(" ".join(sorted(terms)))
    if full and full in haystack:
        score += 1
    return score


def _search_chunks_for_topic(topic: str, doc_ids: list[str] | None) -> list[dict]:
    """在给定文档范围内检索与知识点相关的原文片段。"""
    query = topic.strip()
    if not query:
        return []
    try:
        results = knowledge_service.search(query, limit=6)
    except Exception:
        return []
    if not results:
        return []
    if doc_ids:
        allowed = {str(doc_id) for doc_id in doc_ids if doc_id}
        results = [r for r in results if str(r.get("doc_id") or "") in allowed]
    return results[:3]


def _generated_question_cache_key(
    role_key: str,
    topic_hint: str,
    doc_ids: list[str] | None,
) -> str:
    payload = {
        "role": role_key,
        "topic": (topic_hint or "").strip().lower(),
        "doc_ids": sorted({str(d) for d in (doc_ids or []) if d}),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load_generated_question(cache_key: str) -> dict | None:
    path = GENERATED_QUESTION_CACHE_DIR / f"{cache_key}.json"
    cached = _read_json(path)
    if not cached:
        return None
    if cached.get("version") != GENERATED_QUESTION_CACHE_VERSION:
        return None
    expires_at = int(cached.get("expires_at") or 0)
    if expires_at and expires_at < int(time.time()):
        return None
    return cached.get("payload")


def _save_generated_question(cache_key: str, payload: dict) -> None:
    try:
        ensure_dirs()
        path = GENERATED_QUESTION_CACHE_DIR / f"{cache_key}.json"
        body = {
            "version": GENERATED_QUESTION_CACHE_VERSION,
            "created_at": int(time.time()),
            "expires_at": int(time.time()) + GENERATED_QUESTION_TTL_SECONDS,
            "payload": payload,
        }
        tmp_path = path.with_name(path.name + ".tmp")
        _write_json(tmp_path, body)
        os.replace(tmp_path, path)
    except Exception:
        # 缓存失败不能阻断主流程。
        pass


EXPRESSION_GENERATE_TIMEOUT_SECONDS = 35.0


def _generate_question_via_llm(
    role_name: str,
    topic_hint: str,
    chunks: list[dict],
) -> dict | None:
    """调用 LLM 根据原文片段生成一道表达练习题，失败返回 None。"""
    if not chunks:
        return None
    try:
        raw = llm_service.chat_json(
            prompts.EXPRESSION_GENERATE_SYSTEM,
            prompts.build_expression_generate_user(role_name, topic_hint, chunks),
            max_tokens=1200,
            timeout=EXPRESSION_GENERATE_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    question = str(raw.get("question") or "").strip()
    reference_answer = str(raw.get("reference_answer") or "").strip()
    if not question or not reference_answer:
        return None
    keywords = [str(item).strip() for item in (raw.get("keywords") or []) if str(item).strip()]
    key_points = [
        str(item).strip()
        for item in (raw.get("standard_key_points") or [])
        if str(item).strip()
    ]
    reference_script = str(raw.get("reference_script") or "").strip()
    source_title = str(raw.get("source_title") or "").strip() or (chunks[0].get("title") or "")
    return {
        "question": question,
        "reference_answer": reference_answer,
        "keywords": keywords[:8],
        "standard_key_points": key_points[:5],
        "reference_script": reference_script,
        "source_title": source_title,
    }


def drill_question(
    role_key: str,
    topic_hint: str = "",
    source_doc_ids: list[str] | None = None,
) -> dict:
    """从该岗位的真题库里出一道表达练习题，避开最近练过的题。"""
    role = roles_service.get_role(role_key)
    if not role:
        role_names = "、".join(profile.name for profile in roles_service.ROLE_PROFILES.values())
        raise ExpressionError(f"未知岗位，请选择{role_names}。")
    questions = question_bank.load_role_questions(role.key, limit=60)
    if not questions:
        raise ExpressionError("真题库为空，请先在知识库导入面试题库文档。")

    recent: set[str] = set()
    for session in _recent_sessions(limit=15):
        passed_blind = (
            session.get("role_key") == role.key
            and session.get("practice_mode", "blind") == "blind"
            and (session.get("progression") or {}).get("passed", True)
        )
        if passed_blind:
            recent.add(str(session.get("question_label") or ""))
    pool = [item for item in questions if item.get("label") not in recent] or questions
    topic_terms = _topic_terms(topic_hint)
    topic_matched = False
    if topic_terms:
        topic_pool = [item for item in pool if _topic_matches(item, topic_terms)]
        if topic_pool:
            topic_pool.sort(
                key=lambda item: _topic_match_score(item, topic_terms),
                reverse=True,
            )
            # 只在前 5 道高分题里抽一次，避免宽匹配随机导致看起来错位，
            # 又保留一点点多样性，不会每次都做同一题。
            head = topic_pool[:5] if len(topic_pool) > 5 else topic_pool
            pool = head
            topic_matched = True
    generated_payload: dict | None = None
    generated_by_llm = False
    if (
        not topic_matched
        and topic_hint
        and source_doc_ids
    ):
        cache_key = _generated_question_cache_key(role.key, topic_hint, source_doc_ids)
        cached = _load_generated_question(cache_key)
        if cached:
            generated_payload = cached
        else:
            chunks = _search_chunks_for_topic(topic_hint, source_doc_ids)
            if chunks:
                generated_payload = _generate_question_via_llm(
                    role.name, topic_hint, chunks
                )
                if generated_payload:
                    _save_generated_question(cache_key, generated_payload)
                    _prefetch_speaking_materials(
                        generated_payload["question"],
                        generated_payload["reference_answer"],
                        generated_payload.get("keywords") or [],
                    )
        if generated_payload:
            generated_by_llm = True

    if generated_by_llm and generated_payload:
        keywords, standard_key_points, reference_script = _speaking_materials(
            generated_payload["question"],
            generated_payload["reference_answer"],
            generated_payload.get("keywords") or [],
            allow_llm=False,
        )
        return {
            "question": generated_payload["question"],
            "label": f"AI-{cache_key[:8]}",
            "source": generated_payload.get("source_title") or "",
            "section": "",
            "topic_matched": False,
            "generated_by_llm": True,
            "disclaimer": (
                "本题目由 AI 基于所选资料自动生成，仅供表达练习，"
                "可能与原文表述有差异。"
            ),
            "reference_answer": generated_payload["reference_answer"],
            "keywords": keywords or generated_payload.get("keywords") or [],
            "standard_key_points": standard_key_points,
            "reference_script": reference_script,
            "role_key": role.key,
            "role_name": role.name,
        }

    item = random.choice(pool)
    question = _drill_display_question(item)
    display_question = question or item["question"]
    keywords, standard_key_points, reference_script = _speaking_materials(
        display_question,
        item.get("reference_answer", ""),
        item.get("keywords", []),
        allow_llm=False,
    )
    fallback_disclaimer = None
    if topic_hint and not topic_matched:
        fallback_disclaimer = (
            "本题来自现有真题库随机抽取，未必对应你的薄弱知识点，仅作临时练习。"
        )
    return {
        "question": display_question,
        "label": item.get("label", ""),
        "source": item.get("source", ""),
        "section": item.get("section", ""),
        "topic_matched": topic_matched,
        "generated_by_llm": False,
        "disclaimer": fallback_disclaimer,
        "reference_answer": item.get("reference_answer", ""),
        "keywords": keywords,
        "standard_key_points": standard_key_points,
        "reference_script": reference_script,
        "role_key": role.key,
        "role_name": role.name,
    }


MODE_LABELS = {
    "read": "看答案照读",
    "keywords": "关键词串联",
    "blind": "无提示实战",
}


def progression(metrics: dict, mode: str) -> dict:
    """按训练档位给出下一步，避免把照读得分误当成实战掌握度。"""
    scores = metrics.get("scores") or {}
    fluency = int(scores.get("fluency") or 0)
    structure = int(scores.get("structure") or 0)
    confidence = int(scores.get("confidence") or 0)
    average = round((fluency + structure + confidence) / 3)
    fillers_per_min = float(metrics.get("fillers_per_min") or 0)
    restart_count = int(metrics.get("restart_count") or 0)

    if mode == "read":
        passed = fluency >= 75 and restart_count <= 2 and fillers_per_min <= 8
        next_mode = "keywords" if passed else "read"
        message = (
            "语感已经顺一些了，下一步只保留关键词，用自己的话把同一题串起来。"
            if passed
            else "先别急着脱稿，同一题再跟读一次，重点练停顿和整句稳定输出。"
        )
    elif mode == "keywords":
        passed = structure >= 75 and fluency >= 70 and restart_count <= 3
        next_mode = "blind" if passed else "keywords"
        message = (
            "关键词能串成完整回答了，可以进入无提示模式，按真实面试再来一遍。"
            if passed
            else "关键词之间还需要搭桥，同一题再练一次，先说结论，再按关键词逐个展开。"
        )
    else:
        passed = (
            min(fluency, structure, confidence) >= 72
            and average >= 78
            and fillers_per_min <= 6
            and restart_count <= 2
        )
        next_mode = "next_question" if passed else "keywords"
        message = (
            "这题在无提示下已经比较稳，可以换下一题或进入下一个板块。"
            if passed
            else "实战状态下还不够稳，建议退回关键词模式补一次，再做无提示压测。"
        )

    return {
        "mode": mode,
        "mode_name": MODE_LABELS[mode],
        "passed": passed,
        "next_mode": next_mode,
        "message": message,
    }


def _count_occurrences(text: str, words: tuple[str, ...]) -> Counter:
    counter: Counter = Counter()
    for word in words:
        counter[word] = text.count(word)
    return counter


def compute_metrics(transcript: str, duration_sec: float) -> dict:
    """从转写文本和录音时长算表达指标。全部是确定性计算，不依赖模型。"""
    text = re.sub(r"\s+", "", transcript or "")
    if not text:
        raise ExpressionError("回答内容为空，无法分析。")

    cjk_chars = len(CJK_RE.findall(text))
    duration = max(1.0, float(duration_sec or 0))
    minutes = duration / 60.0

    filler_counter = _count_occurrences(text, FILLER_WORDS)
    crutch_counter = _count_occurrences(text, CRUTCH_WORDS)
    hedge_counter = _count_occurrences(text, HEDGE_WORDS)

    filler_total = sum(filler_counter.values())
    crutch_total = sum(crutch_counter.values())
    hedge_total = sum(hedge_counter.values())

    restart_hits = []
    for match in RESTART_RE.finditer(text):
        token = match.group(1)
        # 单字重叠要排除「谢谢」「常常」这类合法叠词；两字以上的重复基本都是卡顿。
        if len(token) == 1 and token in ("谢", "常", "久", "多", "好", "慢", "快"):
            continue
        restart_hits.append(match.group(0))

    structure_hits = [word for word in STRUCTURE_WORDS if word in text]
    sentence_count = max(1, len(re.findall(r"[。！？!?；;，,]", text)))

    rate_cpm = round(cjk_chars / minutes) if minutes else 0
    fillers_per_min = round((filler_total + crutch_total) / minutes, 1) if minutes else 0
    hedges_per_min = round(hedge_total / minutes, 1) if minutes else 0

    # 三个分项分数，各 100 分。刻意训练初期允许分数低，重点看趋势。
    fluency = 100
    # 语气词每分钟超过 1 个开始明显扣分；口头禅每分钟 3 个以上扣分。
    fluency -= min(55, filler_total / max(minutes, 0.1) * 6)
    fluency -= min(25, max(0, crutch_total / max(minutes, 0.1) - 3) * 4)
    fluency -= min(20, len(restart_hits) * 5)

    structure = 55
    structure += min(25, len(set(structure_hits)) * 8)
    if sentence_count >= 4:
        structure += 10
    if cjk_chars < 80:
        structure -= 15  # 太短通常没有展开
    if cjk_chars > 1200:
        structure -= 10  # 过度冗长也不是好表达

    confidence = 100
    confidence -= min(30, max(0, hedges_per_min - 1) * 5)
    confidence -= min(30, filler_total * 4)
    confidence -= min(20, len(restart_hits) * 5)

    def clamp(value: float) -> int:
        return max(20, min(100, round(value)))

    return {
        "duration_sec": round(duration, 1),
        "char_count": cjk_chars,
        "sentence_count": sentence_count,
        "rate_cpm": rate_cpm,
        "fillers": dict(filler_counter),
        "crutches": dict(crutch_counter),
        "hedges": dict(hedge_counter),
        "filler_total": filler_total,
        "crutch_total": crutch_total,
        "hedge_total": hedge_total,
        "fillers_per_min": fillers_per_min,
        "hedges_per_min": hedges_per_min,
        "restarts": restart_hits[:10],
        "restart_count": len(restart_hits),
        "structure_markers": sorted(set(structure_hits)),
        "scores": {
            "fluency": clamp(fluency),
            "structure": clamp(structure),
            "confidence": clamp(confidence),
        },
    }


def _local_point_review(points: list[dict], transcript: str) -> dict:
    """LLM 失败时用确定性命中兜底：不做语义判断，只识别明显原文命中。"""
    normalized_transcript = _keyword_norm(transcript)
    hit_points: list[dict] = []
    missed_points: list[dict] = []
    for point in points:
        point_id = str(point.get("point_id") or "")
        text = str(point.get("text") or "")
        if _keyword_norm(text) in normalized_transcript:
            hit_points.append({
                "point_id": point_id,
                "text": text,
                "evidence": "回答中明确提到了该要点。",
            })
        else:
            missed_points.append({
                "point_id": point_id,
                "text": text,
                "evidence": "未在回答中识别到该要点。",
            })
    return _build_point_review(hit_points, missed_points, points, "")


def _build_point_review(
    hit_points: list[dict],
    missed_points: list[dict],
    points: list[dict],
    error: str = "",
) -> dict:
    by_id = {str(item.get("point_id")): str(item.get("text") or "") for item in points}
    allowed_ids = set(by_id)
    seen: set[str] = set()

    def normalize(items: object, is_hit: bool) -> list[dict]:
        result: list[dict] = []
        if not isinstance(items, list):
            return result
        for raw in items:
            if not isinstance(raw, dict):
                continue
            point_id = str(raw.get("point_id") or "").strip().upper()
            if point_id not in allowed_ids or point_id in seen:
                continue
            evidence = re.sub(r"\s+", " ", str(raw.get("evidence") or "")).strip()
            if len(evidence) > 60:
                evidence = evidence[:57].rstrip() + "..."
            seen.add(point_id)
            result.append({
                "point_id": point_id,
                "text": by_id[point_id],
                "evidence": evidence or ("命中该要点。" if is_hit else "未讲清该要点。"),
            })
        return result

    normalized_hits = normalize(hit_points, True)
    normalized_missed = normalize(missed_points, False)
    for point in points:
        point_id = str(point.get("point_id"))
        if point_id in seen:
            continue
        normalized_missed.append({
            "point_id": point_id,
            "text": by_id[point_id],
            "evidence": "模型未给出有效判定，按未命中处理。",
        })

    total = len(points)
    hit_count = len(normalized_hits)
    return {
        "hit_points": normalized_hits,
        "missed_points": normalized_missed,
        "hit_count": hit_count,
        "total_points": total,
        "coverage_rate": round(hit_count * 100 / total) if total else 0,
        "error": error,
    }


def _parse_point_review(raw: object, points: list[dict]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    review = raw.get("point_review")
    if not isinstance(review, dict):
        return None
    normalized = _build_point_review(
        review.get("hit_points"),
        review.get("missed_points"),
        points,
    )
    # 有些模型会漏填 missed_points；这里补齐剩余要点，避免前端覆盖率虚高。
    if len(normalized.get("hit_points", [])) + len(normalized.get("missed_points", [])) != len(points):
        return None
    return normalized


def _coverage_summary(review: dict | None) -> dict:
    if not review or not review.get("total_points"):
        return {"hit_count": 0, "total_points": 0, "coverage_rate": 0}
    return {
        "hit_count": int(review.get("hit_count") or 0),
        "total_points": int(review.get("total_points") or 0),
        "coverage_rate": float(review.get("coverage_rate") or 0),
    }


def _load_prior_session(session_id: str, role_key: str, question: str, question_label: str) -> dict | None:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id or ""):
        return None
    prior = _read_json(SESSION_DIR / f"{session_id}.json")
    if not prior:
        return None
    same_role = prior.get("role_key") == role_key
    same_question = (
        prior.get("question") == question
        and (prior.get("question_label") or "") == question_label
    )
    return prior if same_role and same_question else None


def _initial_session_for(prior: dict) -> dict:
    seen = {prior.get("session_id")}
    current = prior
    while True:
        previous_id = str(current.get("previous_session_id") or "")
        if not re.fullmatch(r"[a-f0-9]{32}", previous_id) or previous_id in seen:
            return current
        previous = _read_json(SESSION_DIR / f"{previous_id}.json")
        if not previous or previous.get("role_key") != current.get("role_key"):
            return current
        seen.add(previous_id)
        current = previous


def _review_and_coach(
    role_name: str,
    question: str,
    transcript: str,
    metrics: dict,
    practice_mode: str = "blind",
    points: list[dict] | None = None,
    reference_script: str = "",
    previous_missed: list[str] | None = None,
) -> dict:
    points = points or []
    fallback_review = _local_point_review(points, transcript)
    fallback_coach = {
        "summary": "本次教练建议生成失败，但客观指标已记录，可以先按指标自查。",
        "strengths": [],
        "fixes": [],
        "example": "",
        "next_focus": "下一轮先刻意降低语气词，开口前停半秒。",
        "mindset_tip": "",
    }
    try:
        raw = llm_service.chat_json(
            prompts.EXPRESSION_REVIEW_SYSTEM,
            prompts.build_expression_review_user(
                role_name,
                question,
                transcript,
                metrics,
                MODE_LABELS.get(practice_mode, "无提示实战"),
                points,
                reference_script,
                previous_missed,
            ),
            max_tokens=1800,
        )
    except llm_service.LlmError as exc:
        fallback_coach["coach_error"] = str(exc)
        fallback_review = _local_point_review(points, transcript)
        fallback_review["error"] = "要点复核暂时失败，已用本地命中检查。"
        return {"point_review": fallback_review, "coach": fallback_coach}

    point_review = _parse_point_review(raw.get("point_review"), points)
    coach_raw = raw.get("coach") if isinstance(raw.get("coach"), dict) else {}

    def items(key: str) -> list[str]:
        return [str(value).strip() for value in (coach_raw.get(key) or []) if str(value).strip()]

    coach = {
        "summary": str(coach_raw.get("summary") or "").strip() or fallback_coach["summary"],
        "strengths": items("strengths")[:3],
        "fixes": items("fixes")[:4],
        "example": str(coach_raw.get("example") or "").strip(),
        "next_focus": str(coach_raw.get("next_focus") or "").strip() or fallback_coach["next_focus"],
        "mindset_tip": str(coach_raw.get("mindset_tip") or "").strip(),
    }
    if point_review is None:
        point_review = fallback_review
        point_review["error"] = "模型要点判定不完整，已用本地命中检查。"
    return {"point_review": point_review, "coach": coach}


def _coach(
    role_name: str,
    question: str,
    transcript: str,
    metrics: dict,
    practice_mode: str = "blind",
) -> dict:
    try:
        raw = llm_service.chat_json(
            prompts.EXPRESSION_SYSTEM,
            prompts.build_expression_user(
                role_name,
                question,
                transcript,
                metrics,
                MODE_LABELS.get(practice_mode, "无提示实战"),
            ),
        )
    except llm_service.LlmError as exc:
        return {
            "summary": "本次教练建议生成失败，但客观指标已记录，可以先按指标自查。",
            "strengths": [],
            "fixes": [],
            "example": "",
            "next_focus": "下一轮先刻意降低语气词，开口前停半秒。",
            "mindset_tip": "",
            "coach_error": str(exc),
        }

    def items(key: str) -> list[str]:
        return [str(v).strip() for v in (raw.get(key) or []) if str(v).strip()]

    return {
        "summary": str(raw.get("summary") or "").strip(),
        "strengths": items("strengths")[:3],
        "fixes": items("fixes")[:4],
        "example": str(raw.get("example") or "").strip(),
        "next_focus": str(raw.get("next_focus") or "").strip(),
        "mindset_tip": str(raw.get("mindset_tip") or "").strip(),
    }


def analyze(
    role_key: str,
    question: str,
    question_label: str,
    transcript: str,
    duration_sec: float,
    practice_mode: str = "blind",
    standard_key_points: list[str] | None = None,
    reference_script: str = "",
    previous_session_id: str = "",
) -> dict:
    role = roles_service.get_role(role_key)
    if not role:
        raise ExpressionError("未知岗位。")
    if practice_mode not in MODE_LABELS:
        raise ExpressionError("未知训练模式。")
    prior = _load_prior_session(previous_session_id, role.key, question, question_label)
    # 重述遗漏要点属于刻意训练，后端也强制 blind，防止前端状态被绕过。
    if prior:
        practice_mode = "blind"
    metrics = compute_metrics(transcript, duration_sec)
    step = progression(metrics, practice_mode)

    valid_points: list[str] = []
    seen_points: set[str] = set()
    for value in standard_key_points or []:
        text = str(value).strip()
        normalized = _keyword_norm(text)
        if text and normalized not in seen_points:
            valid_points.append(text)
            seen_points.add(normalized)
    if prior and not valid_points:
        valid_points = [str(item) for item in (prior.get("standard_key_points") or []) if str(item).strip()]
    if not reference_script and prior:
        reference_script = str(prior.get("reference_script") or "")

    point_payload = [
        {"point_id": f"P{index}", "text": text}
        for index, text in enumerate(valid_points[:5], start=1)
    ]
    previous_review = prior.get("point_review") if prior else None
    previous_missed = [
        str(item.get("text") or "").strip()
        for item in ((previous_review or {}).get("missed_points") or [])
        if str(item.get("text") or "").strip()
    ] if prior else []
    review = _review_and_coach(
        role.name,
        question,
        transcript,
        metrics,
        practice_mode,
        point_payload,
        reference_script,
        previous_missed,
    )
    coach = review["coach"]

    attempt_no = 1
    practice_group_id = ""
    if prior:
        attempt_no = int(prior.get("attempt_no") or 1) + 1
        practice_group_id = str(prior.get("practice_group_id") or prior.get("session_id") or "")

    point_review = review["point_review"]
    comparison = None
    initial = _initial_session_for(prior) if prior else None
    if initial and initial.get("point_review") and point_payload:
        previous_summary = _coverage_summary(initial.get("point_review"))
        current_summary = _coverage_summary(point_review)
        comparison = {
            "previous_attempt_no": int(initial.get("attempt_no") or 1),
            "current_attempt_no": attempt_no,
            "previous": previous_summary,
            "current": current_summary,
            "still_missed": [
                str(item.get("text") or "")
                for item in point_review.get("missed_points", [])
                if str(item.get("text") or "")
            ],
        }

    session = {
        "session_id": uuid.uuid4().hex,
        "created_at": int(time.time()),
        "practice_date": date.today().isoformat(),
        "role_key": role.key,
        "role_name": role.name,
        "practice_mode": practice_mode,
        "question": question,
        "question_label": question_label,
        "attempt_no": attempt_no,
        "previous_session_id": prior.get("session_id", "") if prior else "",
        "practice_group_id": practice_group_id or "",
        "standard_key_points": [item["text"] for item in point_payload],
        "reference_script": reference_script,
        "point_review": point_review,
        "comparison": comparison,
        "transcript": transcript.strip()[:6000],
        "metrics": metrics,
        "progression": step,
        "coach": coach,
    }
    ensure_dirs()
    _write_json(SESSION_DIR / f"{session['session_id']}.json", session)
    return session


def _recent_sessions(limit: int = 30) -> list[dict]:
    ensure_dirs()
    sessions = []
    for path in SESSION_DIR.glob("*.json"):
        data = _read_json(path)
        if data:
            sessions.append(data)
    sessions.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return sessions[:limit]


def list_sessions(limit: int = 30) -> list[dict]:
    sessions = _recent_sessions(limit)
    return [
        {
            "session_id": item["session_id"],
            "created_at": item.get("created_at", 0),
            "practice_date": item.get("practice_date", ""),
            "role_name": item.get("role_name", ""),
            "practice_mode": item.get("practice_mode", "blind"),
            "question": item.get("question", ""),
            "metrics": item.get("metrics", {}),
            "progression": item.get("progression") or {},
        }
        for item in sessions
    ]


def progress() -> dict:
    """汇总练习天数、连续打卡和分数趋势，让进步看得见。"""
    sessions = _recent_sessions(limit=500)
    today = date.today()
    practiced_days = {item.get("practice_date") for item in sessions if item.get("practice_date")}

    streak = 0
    cursor = today
    while cursor.isoformat() in practiced_days:
        streak += 1
        cursor = datetime.fromordinal(cursor.toordinal() - 1).date()

    today_count = sum(1 for item in sessions if item.get("practice_date") == today.isoformat())

    trend = []
    for item in reversed(sessions[-20:]):
        scores = (item.get("metrics") or {}).get("scores") or {}
        metrics = item.get("metrics") or {}
        trend.append(
            {
                "date": item.get("practice_date", ""),
                "created_at": item.get("created_at", 0),
                "practice_mode": item.get("practice_mode", "blind"),
                "fluency": scores.get("fluency", 0),
                "structure": scores.get("structure", 0),
                "confidence": scores.get("confidence", 0),
                "rate_cpm": metrics.get("rate_cpm", 0),
                "fillers_per_min": metrics.get("fillers_per_min", 0),
            }
        )

    averages = {}
    if sessions:
        for key in ("fluency", "structure", "confidence"):
            values = [
                (item.get("metrics") or {}).get("scores", {}).get(key)
                for item in sessions
                if (item.get("metrics") or {}).get("scores", {}).get(key)
            ]
            averages[key] = round(sum(values) / len(values)) if values else 0

    mode_counts = {"read": 0, "keywords": 0, "blind": 0}
    for item in sessions:
        mode = item.get("practice_mode", "blind")
        if mode in mode_counts:
            mode_counts[mode] += 1

    blind_items = [item for item in sessions if item.get("practice_mode", "blind") == "blind"]
    blind_average = 0
    if blind_items:
        blind_scores = [
            sum((item.get("metrics") or {}).get("scores", {}).get(key, 0) for key in ("fluency", "structure", "confidence")) / 3
            for item in blind_items
            if (item.get("metrics") or {}).get("scores")
        ]
        blind_average = round(sum(blind_scores) / len(blind_scores)) if blind_scores else 0

    return {
        "total_sessions": len(sessions),
        "practice_days": len(practiced_days),
        "streak_days": streak,
        "today_count": today_count,
        "daily_goal": 3,
        "mode_counts": mode_counts,
        "blind_total": len(blind_items),
        "blind_average": blind_average,
        "averages": averages,
        "trend": trend,
    }
