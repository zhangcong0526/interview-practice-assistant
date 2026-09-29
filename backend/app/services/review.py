"""错题复盘的独立调度状态。

错题本负责“当前是否需要巩固”，复盘文件负责“什么时候再看、看几次后毕业”。
两份数据分开，是为了保持普通刷题连续答对两次移出错题本的原有行为。
"""

import hashlib
import json
import re
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from ..config import settings


REVIEW_FILE = settings.data_dir / "quiz" / "review_schedule.json"
REVIEW_STAGES = (1, 3, 7, 15)
ERROR_CAUSES = ("概念不清", "概念混淆", "粗心", "超纲")


class ReviewError(RuntimeError):
    pass


def _normalise_topic_id(topic: str) -> str:
    text = unicodedata.normalize("NFKC", str(topic or "")).strip().casefold()
    return re.sub(r"[\s·•:：,，。；;、/\\|\-—_（）()\[\]【】{}\"'“”‘’!?！？]+", "", text) or "uncategorised"


def _question_id(topic: str, qtype: str, stem: str, correct_answer: list[str]) -> str:
    payload = "\u0000".join(
        [
            _normalise_topic_id(topic),
            str(qtype or "").strip().lower(),
            unicodedata.normalize("NFKC", str(stem or "")).strip(),
            ",".join(sorted(str(key).upper() for key in correct_answer or [])),
        ]
    )
    return "q_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _start_of_next_day(now: int | None = None) -> int:
    now = int(now if now is not None else time.time())
    next_day = datetime.fromtimestamp(now) + timedelta(days=1)
    return int(next_day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _load_state() -> dict:
    if not REVIEW_FILE.exists():
        return {"schema_version": 1, "records": []}
    try:
        state = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": 1, "records": []}
    if not isinstance(state, dict) or not isinstance(state.get("records"), list):
        return {"schema_version": 1, "records": []}
    return {
        "schema_version": 1,
        "records": [item for item in state["records"] if isinstance(item, dict)],
    }


def _save_state(state: dict) -> None:
    REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _record_by_question_id(records: list[dict], question_id: str) -> dict | None:
    return next((item for item in records if item.get("question_id") == question_id), None)


def _base_record(question: dict, paper: dict, now: int) -> dict:
    topic = str(question.get("topic") or "未分类")
    qtype = str(question.get("type") or "single")
    correct_answer = list(question.get("correct_answer") or [])
    stem = str(question.get("stem") or "")
    source_doc_id = str(question.get("source_doc_id") or "")
    source_docs = list(question.get("source_docs") or [])
    if not source_doc_id and source_docs:
        source_doc_id = str((source_docs[0] or {}).get("doc_id") or "")
    return {
        "review_id": "rv_" + uuid.uuid4().hex,
        "question_id": _question_id(topic, qtype, stem, correct_answer),
        "question_key": stem[:120],
        "topic": topic,
        "topic_id": _normalise_topic_id(topic),
        "type": qtype,
        "stem": stem,
        "options": list(question.get("options") or []),
        "correct_answer": correct_answer,
        "correct_answer_text": str(question.get("correct_answer_text") or ""),
        "explanation": str(question.get("explanation") or ""),
        "source_title": str(question.get("source_title") or ""),
        "source_lineage": {
            "paper_id": str(paper.get("paper_id") or ""),
            "source_doc_id": source_doc_id,
            "source_docs": source_docs,
            "source_chunk_ids": list(question.get("source_chunk_ids") or []),
            "paper_doc_ids": list(paper.get("doc_ids") or []),
            "paper_doc_titles": list(paper.get("doc_titles") or []),
        },
        "status": "active",
        "review_stage": 0,
        "review_pass_count": 0,
        "next_review_at": _start_of_next_day(now),
        "last_review_at": 0,
        "error_causes_history": [],
        "wrong_count": 1,
        "graduated_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def _record_from_mistake(item: dict, now: int) -> dict:
    paper = {
        "paper_id": str(item.get("paper_id") or ""),
        "doc_ids": list(item.get("paper_doc_ids") or []),
        "doc_titles": list(item.get("paper_doc_titles") or []),
    }
    question = {
        **item,
        "source_doc_id": str(item.get("source_doc_id") or ""),
        "source_docs": list(item.get("source_docs") or []),
        "source_chunk_ids": list(item.get("source_chunk_ids") or []),
        "correct_answer_text": item.get("correct_answer_text", ""),
    }
    record = _base_record(question, paper, now)
    record["wrong_count"] = max(1, int(item.get("wrong_count") or 1))
    record["created_at"] = int(item.get("created_at") or now)
    # 历史错题只补齐调度，不虚构曾经复习过的结果。
    record["next_review_at"] = 0
    return record


def migrate_legacy_mistakes() -> int:
    """把没有复盘记录的历史错题补进调度表；只新增记录，不删除数据。"""
    from . import quiz as quiz_service

    mistakes = quiz_service.list_mistakes(limit=10_000)
    state = _load_state()
    records = state["records"]
    known_ids = {item.get("question_id") for item in records}
    known_keys = {item.get("question_key") for item in records}
    now = int(time.time())
    created = 0
    for item in mistakes:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        topic = str(item.get("topic") or "未分类")
        qid = _question_id(
            topic,
            str(item.get("type") or "single"),
            str(item.get("stem") or ""),
            list(item.get("correct_answer") or []),
        )
        key = str(item.get("key"))
        if qid in known_ids or key in known_keys:
            continue
        record = _record_from_mistake(item, now)
        records.append(record)
        known_ids.add(qid)
        known_keys.add(key)
        created += 1
    if created:
        _save_state(state)
    return created


def sync_wrong_answers(graded: list[dict], paper: dict) -> None:
    """普通交卷后同步错题；答对只保留现有错题本行为，不推进复盘。"""
    state = _load_state()
    records = state["records"]
    now = int(time.time())
    next_review_at = _start_of_next_day(now)
    changed = False

    for question in graded:
        if not isinstance(question, dict) or question.get("is_correct"):
            continue
        qid = _question_id(
            str(question.get("topic") or "未分类"),
            str(question.get("type") or "single"),
            str(question.get("stem") or ""),
            list(question.get("correct_answer") or []),
        )
        record = _record_by_question_id(records, qid)
        if not record:
            record = _base_record(question, paper, now)
            records.append(record)
            changed = True
        else:
            changed = True
            record["wrong_count"] = max(1, int(record.get("wrong_count") or 0) + 1)
            record["status"] = "active"
            record["review_stage"] = 0
            record["review_pass_count"] = 0
            record["created_at"] = int(record.get("created_at") or now)
            for key in ("correct_answer_text", "explanation", "user_answer_text"):
                if question.get(key):
                    record[key] = str(question[key])

        record["next_review_at"] = next_review_at
        record["user_answer_text"] = str(question.get("user_answer_text") or "")
        record["updated_at"] = now

    if changed:
        _save_state(state)


def archive_by_question_key(key: str) -> None:
    """手动移出错题本时保留复盘履历，只归档待复习状态。"""
    state = _load_state()
    changed = False
    now = int(time.time())
    for record in state["records"]:
        if record.get("question_key") == key and record.get("status") != "archived":
            record["status"] = "archived"
            record["updated_at"] = now
            changed = True
    if changed:
        _save_state(state)


def complete_review_practice(graded: list[dict], paper: dict) -> dict | None:
    """复盘练习整卷通过才推进；任一题错则回一天。"""
    context = paper.get("generation_context") if isinstance(paper.get("generation_context"), dict) else {}
    if context.get("kind") != "review_practice":
        return None
    review_ids = {str(item) for item in context.get("review_ids") or [] if item}
    if not review_ids:
        return None

    state = _load_state()
    records = [item for item in state["records"] if item.get("review_id") in review_ids]
    if not records:
        return None

    passed = bool(graded) and all(bool(item.get("is_correct")) for item in graded)
    now = int(time.time())
    for record in records:
        record["last_review_at"] = now
        record["updated_at"] = now
        if not passed:
            record["status"] = "active"
            record["review_stage"] = 0
            record["review_pass_count"] = 0
            record["next_review_at"] = _start_of_next_day(now)
            continue

        record["review_pass_count"] = int(record.get("review_pass_count") or 0) + 1
        record["review_stage"] = min(len(REVIEW_STAGES), int(record.get("review_stage") or 0) + 1)
        if record["review_pass_count"] >= len(REVIEW_STAGES):
            record["status"] = "graduated"
            record["graduated_count"] = int(record.get("graduated_count") or 0) + 1
            record["next_review_at"] = 0
        else:
            record["next_review_at"] = now + REVIEW_STAGES[record["review_stage"] - 1] * 86_400
    _save_state(state)
    return {"passed": passed, "review_count": len(records)}


def sync_grade(graded: list[dict], paper: dict) -> dict | None:
    context = paper.get("generation_context") if isinstance(paper.get("generation_context"), dict) else {}
    if context.get("kind") == "review_practice":
        return complete_review_practice(graded, paper)
    sync_wrong_answers(graded, paper)
    return None


def update_error_cause(review_id: str, cause: str) -> dict:
    if cause not in ERROR_CAUSES:
        raise ReviewError("错因必须是：概念不清、概念混淆、粗心、超纲。")
    state = _load_state()
    record = next((item for item in state["records"] if item.get("review_id") == review_id), None)
    if not record:
        raise ReviewError("复盘记录不存在。")
    history = [item for item in record.get("error_causes_history") or [] if isinstance(item, dict)]
    history.append({"cause": cause, "marked_at": int(time.time())})
    record["error_causes_history"] = history[-20:]
    record["updated_at"] = int(time.time())
    _save_state(state)
    return _public_record(record)


def _public_record(record: dict) -> dict:
    copied = dict(record)
    copied["error_causes"] = [
        str(item.get("cause") or "")
        for item in (record.get("error_causes_history") or [])
        if isinstance(item, dict) and item.get("cause")
    ]
    return copied


def list_records() -> list[dict]:
    migrate_legacy_mistakes()
    return [_public_record(item) for item in _load_state()["records"]]


def get_dashboard() -> dict:
    migrate_legacy_mistakes()
    records = _load_state()["records"]
    now = int(time.time())
    due = [
        item
        for item in records
        if item.get("status") == "active" and int(item.get("next_review_at") or 0) <= now
    ]
    tasks: dict[str, dict] = {}
    for record in due:
        topic_id = str(record.get("topic_id") or _normalise_topic_id(record.get("topic") or ""))
        task = tasks.setdefault(
            topic_id,
            {
                "topic_id": topic_id,
                "topic": record.get("topic") or "未分类",
                "due_count": 0,
                "review_ids": [],
                "next_review_at": 0,
                "source_docs": [],
                "records": [],
            },
        )
        task["due_count"] += 1
        task["review_ids"].append(record.get("review_id"))
        task["records"].append(_public_record(record))
        lineage = record.get("source_lineage") if isinstance(record.get("source_lineage"), dict) else {}
        for title in lineage.get("paper_doc_titles") or []:
            if title and title not in task["source_docs"]:
                task["source_docs"].append(title)

    sorted_tasks = sorted(
        tasks.values(),
        key=lambda item: (-item["due_count"], item["topic"]),
    )
    return {
        "due_count": len(due),
        "active_count": sum(item.get("status") == "active" for item in records),
        "graduated_count": sum(item.get("status") == "graduated" for item in records),
        "archived_count": sum(item.get("status") == "archived" for item in records),
        "tasks": sorted_tasks,
        "updated_at": now,
    }


def get_knowledge_map() -> dict:
    from . import quiz as quiz_service

    return quiz_service.get_progress()


def generate_practice_paper(topic_id: str, total: int = 5) -> dict:
    """为一个知识点生成 5 道复盘变形题，并绑定本次要推进的复盘记录。"""
    from . import knowledge as knowledge_service
    from . import quiz as quiz_service

    migrate_legacy_mistakes()
    now = int(time.time())
    records = [
        item
        for item in _load_state()["records"]
        if item.get("topic_id") == topic_id
        and item.get("status") == "active"
        and int(item.get("next_review_at") or 0) <= now
    ]
    if not records:
        raise ReviewError("这个知识点当前没有到期待复盘的错题。")
    if total != 5:
        raise ReviewError("复盘练习固定一次 5 题。")

    topic = records[0].get("topic") or "未分类"
    valid_doc_ids = {
        str(doc.get("doc_id") or "")
        for doc in knowledge_service.list_documents()
        if doc.get("doc_id")
    }
    doc_ids: list[str] = []
    for record in records:
        lineage = record.get("source_lineage") if isinstance(record.get("source_lineage"), dict) else {}
        docs = record.get("source_docs") if isinstance(record.get("source_docs"), list) else []
        candidates = [str(lineage.get("source_doc_id") or "")]
        candidates.extend(str(doc.get("doc_id") or "") for doc in docs if isinstance(doc, dict))
        for doc_id in candidates:
            if doc_id in valid_doc_ids and doc_id not in doc_ids:
                doc_ids.append(doc_id)

    # 早期错题可能只保留了标题血缘，先做同标题兜底，最后让关键词在库内检索。
    if not doc_ids:
        titles = []
        for record in records:
            if record.get("source_title"):
                titles.append(str(record["source_title"]))
            lineage = record.get("source_lineage") if isinstance(record.get("source_lineage"), dict) else {}
            titles.extend(str(title) for title in lineage.get("paper_doc_titles") or [] if title)
        doc_ids = [
            str(doc["doc_id"])
            for doc in knowledge_service.list_documents()
            if doc.get("doc_id") and doc.get("title") in set(titles) and doc["doc_id"] not in doc_ids
        ]

    paper = quiz_service.generate_paper(
        [topic],
        doc_ids,
        single=2,
        multiple=1,
        judge=2,
        difficulty="mixed",
        focus_weak=True,
    )
    paper["generation_context"] = {
        "kind": "review_practice",
        "topic_id": topic_id,
        "topic": topic,
        "review_ids": [item.get("review_id") for item in records],
        "requested_total": total,
    }
    quiz_service._write_json(
        quiz_service.PAPERS_DIR / f"{paper['paper_id']}.json",
        paper,
    )
    return paper
