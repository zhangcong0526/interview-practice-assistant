import json
import re
import time
import uuid
from pathlib import Path

from ..config import settings
from .. import prompts
from . import knowledge as knowledge_service
from . import llm as llm_service


class ResumeError(RuntimeError):
    pass


RESUME_DIR = settings.data_dir / "resume"
PROFILES_DIR = RESUME_DIR / "profiles"
PARSING_DIR = RESUME_DIR / "parsing"
ACTIVE_POINTER = RESUME_DIR / "active.json"
MAX_RESUME_CHARS = 60_000
# 短于这个长度就一次性解析；更长的走分段，避免输出撞上 max_tokens 被截断。
SINGLE_PASS_CHARS = 6_000
SEGMENT_CHARS = 4_500
PARSE_MAX_TOKENS = 8_000

# 简历中常见的板块标题，用来找自然切分点，避免把一个项目劈成两半。
SECTION_RE = re.compile(
    r"^\s*(?:[#★☆■□●○◆◇▎|【\[]*\s*)?"
    r"(?:个人|基本)?(?:信息|简介|资料)"
    r"|^\s*[#【\[]*\s*(?:专业|个人)?技能"
    r"|^\s*[#【\[]*\s*(?:工作|实习)(?:经历|经验|背景)"
    r"|^\s*[#【\[]*\s*项目(?:经历|经验)"
    r"|^\s*[#【\[]*\s*(?:教育|学习)(?:经历|背景)"
    r"|^\s*[#【\[]*\s*(?:证书|荣誉|奖项|自我评价|其他)"
    r"|^\s*项目\s*(?:名称)?\s*[一二三四五六七八九十]+\s*[:：]"
    r"|^\s*项目\s*(?:名称)?\s*\d+\s*[:：]"
    r"|^\s*项目名称\s*[:：]"
    r"|^\s*#{1,4}\s+",
    re.MULTILINE,
)


def ensure_dirs() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    PARSING_DIR.mkdir(parents=True, exist_ok=True)


def _profile_path(resume_id: str) -> Path:
    return PROFILES_DIR / f"{resume_id}.json"


def extract_resume_text(path: Path, filename: str) -> str:
    """复用知识库的文档解析能力。"""
    try:
        text = knowledge_service.extract_text(path, filename)
    except knowledge_service.KnowledgeError as exc:
        raise ResumeError(str(exc)) from exc
    if not text.strip():
        raise ResumeError("未能从简历中提取到文字，可能是扫描版 PDF，请改用粘贴文本。")
    return text[:MAX_RESUME_CHARS]


def _normalise_profile(profile: dict) -> dict:
    """LLM 输出结构可能有缺失，这里补齐为稳定形状。"""
    def as_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    projects = []
    for item in profile.get("projects") or []:
        if not isinstance(item, dict):
            continue
        projects.append(
            {
                "name": str(item.get("name") or "").strip(),
                "role": str(item.get("role") or "").strip(),
                "period": str(item.get("period") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "tech_stack": as_list(item.get("tech_stack")),
                "achievements": as_list(item.get("achievements")),
                "metrics": as_list(item.get("metrics")),
                "gaps": as_list(item.get("gaps")),
            }
        )

    experiences = []
    for item in profile.get("experiences") or []:
        if not isinstance(item, dict):
            continue
        experiences.append(
            {
                "company": str(item.get("company") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "period": str(item.get("period") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
            }
        )

    try:
        years = int(profile.get("years_of_experience") or 0)
    except (TypeError, ValueError):
        years = 0

    return {
        "name": str(profile.get("name") or "").strip(),
        "headline": str(profile.get("headline") or "").strip(),
        "years_of_experience": years,
        "skills": as_list(profile.get("skills")),
        "projects": projects,
        "experiences": experiences,
        "education": str(profile.get("education") or "").strip(),
        "highlights": as_list(profile.get("highlights")),
        "risk_points": as_list(profile.get("risk_points")),
    }


def split_resume(text: str, max_chars: int = SEGMENT_CHARS) -> list[str]:
    """按简历的自然板块切段，段落过长时再按空行细分。

    切分点优先落在"工作经历""项目二"这类标题上，
    这样一个项目的描述不会被拦腰截断。
    """
    source = text.strip()
    if not source:
        return []
    if len(source) <= max_chars:
        return [source]

    # 先按板块标题切成完整块：每块 = 一个标题 + 它下面的全部正文。
    marks = [match.start() for match in SECTION_RE.finditer(source)]
    bounds = sorted({0, *marks, len(source)})
    blocks: list[str] = []
    for start, end in zip(bounds, bounds[1:]):
        block = source[start:end].strip()
        if block:
            blocks.append(block)
    if not blocks:
        blocks = [source]

    # 单个板块仍然超长时按空行继续拆。
    pieces: list[str] = []
    for block in blocks:
        if len(block) <= max_chars:
            pieces.append(block)
            continue
        buffer = ""
        for paragraph in re.split(r"\n{2,}", block):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if len(paragraph) > max_chars:
                if buffer:
                    pieces.append(buffer)
                    buffer = ""
                for index in range(0, len(paragraph), max_chars):
                    pieces.append(paragraph[index : index + max_chars])
                continue
            candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
            if len(candidate) <= max_chars:
                buffer = candidate
            else:
                pieces.append(buffer)
                buffer = paragraph
        if buffer:
            pieces.append(buffer)

    # 相邻的小块合并，减少请求次数。
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) + len(piece) + 2 <= max_chars:
            merged[-1] = f"{merged[-1]}\n\n{piece}"
        else:
            merged.append(piece)
    return merged


def _empty_profile() -> dict:
    return _normalise_profile({})


def _merge_profiles(profiles: list[dict]) -> dict:
    """把各段解析结果合并成一份档案，同名项目去重，列表字段按顺序去重。"""
    merged = _empty_profile()
    seen_projects: dict[str, dict] = {}
    seen_experiences: set[tuple] = set()

    def extend_unique(target: list[str], values: list[str]) -> None:
        for value in values:
            if value and value not in target:
                target.append(value)

    for profile in profiles:
        if not merged["name"] and profile.get("name"):
            merged["name"] = profile["name"]
        if not merged["headline"] and profile.get("headline"):
            merged["headline"] = profile["headline"]
        if not merged["education"] and profile.get("education"):
            merged["education"] = profile["education"]
        merged["years_of_experience"] = max(
            merged["years_of_experience"], profile.get("years_of_experience") or 0
        )

        extend_unique(merged["skills"], profile.get("skills") or [])
        extend_unique(merged["highlights"], profile.get("highlights") or [])
        extend_unique(merged["risk_points"], profile.get("risk_points") or [])

        for project in profile.get("projects") or []:
            key = (project.get("name") or "").strip().lower()
            if not key:
                merged["projects"].append(project)
                continue
            existing = seen_projects.get(key)
            if not existing:
                seen_projects[key] = project
                merged["projects"].append(project)
                continue
            # 同一个项目跨段出现时，补齐缺失字段并合并列表。
            for field in ("role", "period", "summary"):
                if not existing.get(field) and project.get(field):
                    existing[field] = project[field]
            for field in ("tech_stack", "achievements", "metrics", "gaps"):
                extend_unique(existing.setdefault(field, []), project.get(field) or [])

        for experience in profile.get("experiences") or []:
            key = (
                experience.get("company", ""),
                experience.get("title", ""),
                experience.get("period", ""),
            )
            if key in seen_experiences:
                continue
            seen_experiences.add(key)
            merged["experiences"].append(experience)

    return merged


def _infer_years(profile: dict, raw_text: str) -> int:
    """分段解析时模型常拿不到总年限，这里从工作经历的时间跨度推算。"""
    # 教育经历的起始年份不算工作年限，优先只看工作经历段落。
    periods: list[str] = []
    for experience in profile.get("experiences") or []:
        period = str(experience.get("period") or "").strip()
        if period:
            periods.append(period)
    scope = " ".join(periods) if periods else raw_text

    found = [int(match.group(0)) for match in re.finditer(r"(?:19|20)\d{2}", scope)]
    if not found:
        return 0

    start = min(found)
    end = max(found)
    if re.search(r"至今|now|present", scope, re.IGNORECASE):
        end = max(end, time.localtime().tm_year)
    span = end - start
    return span if 0 < span <= 50 else 0


def _finalise_profile(profile: dict) -> dict:
    """基于合并结果做一次全局收尾，补 headline、亮点和跨段才能看出的风险点。"""
    try:
        raw = llm_service.chat_json(
            prompts.RESUME_MERGE_SYSTEM,
            prompts.build_resume_merge_user(prompts.format_resume_profile(profile)),
            max_tokens=2_000,
        )
    except llm_service.LlmError:
        # 收尾是锦上添花，失败了也要保住已经抽取到的主体内容。
        return profile

    headline = str(raw.get("headline") or "").strip()
    if headline:
        profile["headline"] = headline

    for field in ("highlights", "risk_points"):
        values = [str(v).strip() for v in (raw.get(field) or []) if str(v).strip()]
        if values:
            profile[field] = values
    return profile


def _parse_one(text: str, index: int, total: int) -> dict:
    if total == 1:
        system = prompts.RESUME_PARSE_SYSTEM
        user = prompts.build_resume_parse_user(text)
    else:
        system = prompts.RESUME_SEGMENT_SYSTEM
        user = prompts.build_resume_segment_user(text, index + 1, total)
    raw = llm_service.chat_json(system, user, max_tokens=PARSE_MAX_TOKENS)
    return _normalise_profile(raw)


def _has_content(profile: dict) -> bool:
    return bool(
        profile.get("name")
        or profile.get("projects")
        or profile.get("skills")
        or profile.get("experiences")
        or profile.get("education")
    )


def parse_resume(text: str, job_id: str = "", on_progress=None) -> dict:
    """把简历正文解析成结构化档案。

    长简历会被切成多段分别解析再合并。每段结果实时落盘，
    中途失败时可以用同一个 job_id 续跑，已完成的段不会重复请求模型。
    """
    source = text.strip()
    if not source:
        raise ResumeError("简历内容为空。")

    segments = (
        [source] if len(source) <= SINGLE_PASS_CHARS else split_resume(source)
    )
    total = len(segments)
    state = _load_parse_state(job_id, total) if job_id else {}
    done: dict[int, dict] = state.get("segments", {}) if state else {}

    failures: list[str] = []
    for index, segment in enumerate(segments):
        if index in done:
            continue
        if on_progress:
            on_progress(index, total)
        try:
            done[index] = _parse_one(segment, index, total)
        except llm_service.LlmError as exc:
            failures.append(f"第 {index + 1} 段：{exc}")
            continue
        if job_id:
            _save_parse_state(job_id, total, done)

    if not done:
        detail = failures[0] if failures else "模型没有返回可用内容。"
        raise ResumeError(f"简历解析失败：{detail}")

    ordered = [done[key] for key in sorted(done)]
    profile = ordered[0] if total == 1 else _merge_profiles(ordered)

    if not _has_content(profile):
        raise ResumeError("简历解析失败：模型没有从简历中抽取到有效信息。")

    if not profile.get("years_of_experience"):
        profile["years_of_experience"] = _infer_years(profile, source)

    if total > 1:
        profile = _finalise_profile(profile)

    if failures:
        # 保留进度，用同一个 job_id 重试时只补失败的那几段。
        profile.setdefault("risk_points", []).append(
            f"简历有 {len(failures)} 段未能解析成功，档案可能不完整，"
            "可以用同一份简历重新上传续跑，已解析的内容不会重复请求。"
        )
    elif job_id:
        _clear_parse_state(job_id)
    return profile


# --------------------------------------------------------------------------
# 断点续传状态
# --------------------------------------------------------------------------


def _parse_state_path(job_id: str) -> Path:
    return PARSING_DIR / f"{job_id}.json"


def _load_parse_state(job_id: str, total: int) -> dict:
    path = _parse_state_path(job_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    # 段数对不上说明简历内容变了，旧进度作废。
    if data.get("total") != total:
        return {}
    segments = {
        int(key): value for key, value in (data.get("segments") or {}).items()
    }
    return {"total": total, "segments": segments}


def _save_parse_state(job_id: str, total: int, segments: dict[int, dict]) -> None:
    ensure_dirs()
    payload = {
        "job_id": job_id,
        "total": total,
        "segments": {str(key): value for key, value in segments.items()},
        "updated_at": int(time.time()),
    }
    _parse_state_path(job_id).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _clear_parse_state(job_id: str) -> None:
    _parse_state_path(job_id).unlink(missing_ok=True)


def save_resume(title: str, raw_text: str, profile: dict, source_type: str) -> dict:
    ensure_dirs()
    resume_id = uuid.uuid4().hex
    record = {
        "resume_id": resume_id,
        "title": title.strip() or "我的简历",
        "source_type": source_type,
        "raw_text": raw_text,
        "profile": profile,
        "char_count": len(raw_text),
        "created_at": int(time.time()),
    }
    _profile_path(resume_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    set_active(resume_id)
    return record


def get_resume(resume_id: str) -> dict:
    path = _profile_path(resume_id)
    if not path.exists():
        raise ResumeError("简历不存在或已删除")
    return json.loads(path.read_text(encoding="utf-8"))


def list_resumes() -> list[dict]:
    ensure_dirs()
    active = get_active_id()
    items: list[dict] = []
    for path in PROFILES_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        profile = record.get("profile") or {}
        items.append(
            {
                "resume_id": record["resume_id"],
                "title": record.get("title", ""),
                "source_type": record.get("source_type", ""),
                "char_count": record.get("char_count", 0),
                "created_at": record.get("created_at", 0),
                "is_active": record["resume_id"] == active,
                "name": profile.get("name", ""),
                "headline": profile.get("headline", ""),
                "project_count": len(profile.get("projects") or []),
            }
        )
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items


def delete_resume(resume_id: str) -> None:
    path = _profile_path(resume_id)
    if not path.exists():
        raise ResumeError("简历不存在或已删除")
    path.unlink()
    if get_active_id() == resume_id:
        ACTIVE_POINTER.unlink(missing_ok=True)


def set_active(resume_id: str) -> None:
    ensure_dirs()
    if not _profile_path(resume_id).exists():
        raise ResumeError("简历不存在或已删除")
    ACTIVE_POINTER.write_text(
        json.dumps({"resume_id": resume_id}), encoding="utf-8"
    )


def get_active_id() -> str:
    if not ACTIVE_POINTER.exists():
        return ""
    try:
        return str(json.loads(ACTIVE_POINTER.read_text(encoding="utf-8")).get("resume_id") or "")
    except json.JSONDecodeError:
        return ""


def get_active_resume() -> dict | None:
    resume_id = get_active_id()
    if not resume_id:
        return None
    try:
        return get_resume(resume_id)
    except ResumeError:
        return None


def update_profile(resume_id: str, profile: dict) -> dict:
    record = get_resume(resume_id)
    record["profile"] = _normalise_profile(profile)
    _profile_path(resume_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def resolve_profile_text(resume_id: str = "") -> str:
    """给面试和分析用的简历档案文本；resume_id 为空时使用当前激活简历。"""
    record: dict | None
    if resume_id:
        try:
            record = get_resume(resume_id)
        except ResumeError:
            record = None
    else:
        record = get_active_resume()
    if not record:
        return ""
    return prompts.format_resume_profile(record.get("profile") or {})


def resolve_profile_dict(resume_id: str = "") -> dict:
    """取结构化简历档案本身，供岗位相关经历提取使用。"""
    record: dict | None
    if resume_id:
        try:
            record = get_resume(resume_id)
        except ResumeError:
            record = None
    else:
        record = get_active_resume()
    if not record:
        return {}
    return record.get("profile") or {}
