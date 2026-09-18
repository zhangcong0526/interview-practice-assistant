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
import uuid
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from ..config import settings
from .. import prompts
from .. import roles as roles_service
from . import llm as llm_service
from . import question_bank


class ExpressionError(RuntimeError):
    pass


SESSION_DIR = settings.data_dir / "expression" / "sessions"
KEYWORD_CACHE_DIR = settings.data_dir / "expression" / "keyword_cache"
KEYWORD_CACHE_VERSION = "speaking-keywords-v2"

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


def _drill_display_question(item: dict) -> str:
    """练习题面和关键词缓存共用同一份清洗结果，避免批注导致缓存键不一致。"""
    question = re.sub(
        r"[（(][^（）()]{0,30}?(?:answers?\.md|第\d+题|改进版|已答|见文档|拓展题|追问)[^（）()]*[）)]\s*$",
        "",
        item["question"],
    ).strip()
    return question or item["question"]


def drill_question(role_key: str) -> dict:
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
    item = random.choice(pool)
    # 真题库原文里偶尔夹着文档批注，如「（answers.md第46题已有）」，练习题面要干净。
    question = _drill_display_question(item)
    display_question = question or item["question"]
    keywords = _speaking_keywords(
        display_question,
        item.get("reference_answer", ""),
        item.get("keywords", []),
    )
    return {
        "question": display_question,
        "label": item.get("label", ""),
        "source": item.get("source", ""),
        "section": item.get("section", ""),
        "reference_answer": item.get("reference_answer", ""),
        "keywords": keywords,
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
) -> dict:
    role = roles_service.get_role(role_key)
    if not role:
        raise ExpressionError("未知岗位。")
    if practice_mode not in MODE_LABELS:
        raise ExpressionError("未知训练模式。")
    metrics = compute_metrics(transcript, duration_sec)
    step = progression(metrics, practice_mode)
    coach = _coach(role.name, question, transcript, metrics, practice_mode)

    session = {
        "session_id": uuid.uuid4().hex,
        "created_at": int(time.time()),
        "practice_date": date.today().isoformat(),
        "role_key": role.key,
        "role_name": role.name,
        "practice_mode": practice_mode,
        "question": question,
        "question_label": question_label,
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
