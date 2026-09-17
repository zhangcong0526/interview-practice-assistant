"""简历优化建议：把三条已有数据交汇，指出简历该改哪里、怎么改。

三条输入都来自系统里已经存在的数据，不引入新数据源：
1. 真题库（「硬件面试题答疑」+「软件+AI+管理流程 薄弱题库」）的题型与频率，
   代表面试官真实爱问什么；
2. 在线刷题的掌握度，代表候选人当前答不好什么；
3. 简历档案解析时沉淀的 gaps 与 risk_points，代表简历本身写得不到位的地方。

输出只是改写建议，绝不替候选人编造任何不存在的经历或数字。
"""

from __future__ import annotations

import time

from ..config import settings
from .. import prompts
from . import knowledge as knowledge_service
from . import llm as llm_service
from . import question_bank
from . import quiz as quiz_service
from . import resume as resume_service


class AdvisorError(RuntimeError):
    pass


ADVICE_DIR = settings.data_dir / "resume" / "advice"
# 真实面试沉淀的两份题库，按标题关键词识别。
BANK_TITLE_HINTS = ("硬件面试题答疑", "薄弱题库")


def _advice_path(resume_id: str):
    ADVICE_DIR.mkdir(parents=True, exist_ok=True)
    return ADVICE_DIR / f"{resume_id}.json"


def _collect_bank_questions(limit: int = 90) -> list[dict]:
    """只从两份真实面试题库文档里抽题，频率统计以它们为准。"""
    questions: list[dict] = []
    for summary in knowledge_service.list_documents():
        title = summary.get("title") or ""
        if not any(hint in title for hint in BANK_TITLE_HINTS):
            continue
        document = knowledge_service.load_document(summary["doc_id"])
        if not document:
            continue
        text = "\n".join(document.get("chunks") or [])
        for item in question_bank.extract_questions(text):
            item["source"] = title
            questions.append(item)
    return questions[:limit]


def _section_frequency(questions: list[dict]) -> dict[str, int]:
    freq: dict[str, int] = {}
    for item in questions:
        section = item.get("section") or "未分节"
        freq[section] = freq.get(section, 0) + 1
    return dict(sorted(freq.items(), key=lambda kv: -kv[1]))


def _resume_facts(profile: dict) -> str:
    """把档案里的项目和风险点整理成模型可读的清单。"""
    lines: list[str] = []
    for index, project in enumerate(profile.get("projects") or [], start=1):
        name = project.get("name") or f"项目{index}"
        lines.append(f"项目{index}：{name}（{project.get('period', '')}，{project.get('role', '')}）")
        if project.get("summary"):
            lines.append(f"  概述：{project['summary']}")
        metrics = [str(m) for m in (project.get("metrics") or []) if m]
        if metrics:
            lines.append(f"  已有量化：{'；'.join(metrics[:4])}")
        gaps = [str(g) for g in (project.get("gaps") or []) if g]
        if gaps:
            lines.append(f"  档案标注的缺口：{'；'.join(gaps[:4])}")
    risks = [str(r) for r in (profile.get("risk_points") or []) if r]
    if risks:
        lines.append("档案标注的整体风险：")
        lines.extend(f"  - {risk}" for risk in risks[:7])
    return "\n".join(lines)


def generate_advice(resume_id: str = "", refresh: bool = False) -> dict:
    record = resume_service.get_active_resume() if not resume_id else resume_service.get_resume(resume_id)
    if not record:
        raise AdvisorError("还没有简历档案，请先导入简历。")
    resume_id = record["resume_id"]
    profile = record.get("profile") or {}

    path = _advice_path(resume_id)
    if path.exists() and not refresh:
        import json

        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("char_count") == record.get("char_count"):
                return cached
        except (json.JSONDecodeError, OSError):
            pass

    questions = _collect_bank_questions()
    if not questions:
        raise AdvisorError("真题库为空，请先在知识库导入面试题库文档。")
    weak_topics = quiz_service.list_weak_topics(limit=10)

    try:
        raw = llm_service.chat_json(
            prompts.RESUME_ADVICE_SYSTEM,
            prompts.build_resume_advice_user(
                _resume_facts(profile),
                questions,
                _section_frequency(questions),
                weak_topics,
            ),
        )
    except llm_service.LlmError as exc:
        raise AdvisorError(f"建议生成失败：{exc}") from exc

    suggestions: list[dict] = []
    for item in raw.get("suggestions") or []:
        if not isinstance(item, dict):
            continue
        suggestion = str(item.get("suggestion") or "").strip()
        if not suggestion:
            continue
        priority = str(item.get("priority") or "medium").strip().lower()
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        suggestions.append(
            {
                "priority": priority,
                "target": str(item.get("target") or "").strip(),
                "issue": str(item.get("issue") or "").strip(),
                "suggestion": suggestion,
                "example": str(item.get("example") or "").strip(),
                "evidence": [str(e).strip() for e in (item.get("evidence") or []) if str(e).strip()][:4],
            }
        )
    if not suggestions:
        raise AdvisorError("模型没有返回有效建议，请重试。")

    order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda item: order.get(item["priority"], 1))

    result = {
        "resume_id": resume_id,
        "char_count": record.get("char_count", 0),
        "suggestions": suggestions,
        "question_count": len(questions),
        "weak_topic_count": len(weak_topics),
        "created_at": int(time.time()),
    }
    import json

    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_advice(resume_id: str) -> dict | None:
    path = _advice_path(resume_id)
    if not path.exists():
        return None
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
