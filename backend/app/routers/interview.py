import asyncio

from fastapi import APIRouter, HTTPException

from .. import prompts
from .. import roles
from ..schemas import InterviewTurnRequest
from ..services import llm as llm_service
from ..services import resume as resume_service
from ..services import question_bank as question_bank_service

router = APIRouter(prefix="/api/interview", tags=["interview"])

MAX_HISTORY_TURNS = 24


@router.get("/roles")
async def list_roles():
    return roles.list_roles()


@router.post("/turn")
async def interview_turn(req: InterviewTurnRequest):
    resume_profile = resume_service.resolve_profile_text(req.resume_id)
    role = roles.get_role(req.role_key)
    role_brief = roles.build_role_brief(role) if role else ""
    bank_brief = ""
    resume_focus = ""
    if role:
        # 真题来自用户自己的面试复盘文档，取不到时静默降级为模型自由出题。
        try:
            bank_brief = question_bank_service.build_question_bank_brief(
                question_bank_service.load_role_questions(role.key)
            )
        except Exception:
            bank_brief = ""
        # 题库覆盖不到的部分，靠简历里与本岗位相关的经历现场出定制题。
        try:
            resume_focus = roles.build_resume_focus(
                role, resume_service.resolve_profile_dict(req.resume_id)
            )
        except Exception:
            resume_focus = ""
    context = prompts.build_interviewer_context(
        req.jd,
        req.resume,
        req.knowledge,
        resume_profile,
        role_brief,
        bank_brief,
        resume_focus,
    )
    history = [item for item in req.history[-MAX_HISTORY_TURNS:] if item.content.strip()]
    remaining = req.max_questions - req.asked_count

    if not history:
        instruction = (
            "现在面试开始，请说开场白。严格遵守系统提示里的开场白规范："
            "先问好并自报身份，再用一句话点明本场面试主要聊哪几个方面，"
            "最后请候选人用两三分钟做自我介绍。"
            "这一轮不要提任何技术问题，也不要点名简历里的具体项目或数字。"
            "方向最多说两个，全段不含标点不超过 80 字，写完自己数一遍，超了就删掉一个方向再输出。"
        )
    elif remaining <= 0:
        instruction = "提问数量已达上限，请说一段简短的结束语，并把 is_final 设为 true。"
    else:
        instruction = (
            f"请根据候选人最后的回答继续面试。还可以提问约 {remaining} 个主要问题。"
            "如果回答不够具体或缺少量化结果，请就该点追问。"
        )

    # 实测 DeepSeek 在 json_object 模式下，若历史包含多轮 assistant 消息会返回空白内容。
    # 因此把对话历史折叠成一段文本，始终只发送 system + 单条 user 消息。
    parts = [context]
    if history:
        transcript = "\n".join(
            ("面试官" if item.role == "interviewer" else "候选人") + "：" + item.content.strip()
            for item in history
        )
        parts.append("【已进行的对话】\n" + transcript)
    parts.append(instruction)

    messages: list[dict] = [
        {"role": "system", "content": prompts.INTERVIEWER_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]

    try:
        data = await asyncio.to_thread(llm_service.chat_json_messages, messages)
    except llm_service.LlmError as exc:
        raise HTTPException(502, str(exc))

    speech = str(data.get("speech") or "").strip()
    if not speech:
        raise HTTPException(502, "面试官未返回有效内容，请重试。")

    return {
        "speech": speech,
        "is_new_question": bool(data.get("is_new_question", True)),
        "is_final": bool(data.get("is_final", False)) or remaining <= 0,
        "note": str(data.get("note") or ""),
    }
