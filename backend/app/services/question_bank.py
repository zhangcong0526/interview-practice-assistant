"""真题题库抽取。

用户把历次真实面试沉淀成了带编号的题库文档：「硬件面试题答疑」用 Q1~Qn
编号，「软件+AI+管理流程 薄弱题库」用 A1/B1/C1 这样的字母分段编号。
这些题比模型自由发挥更有实战价值，模拟面试时按岗位取一批塞进面试官
上下文，让它优先复用真题而不是凭空造题。
"""

from __future__ import annotations

import re

from . import knowledge as knowledge_service

# 两种编号写法：Q12：xxx / A1. xxx
QUESTION_RES = (
    re.compile(r"^#{0,6}\s*(Q\d+)\s*[：:．.、]\s*(.+?)\s*$"),
    re.compile(r"^#{0,6}\s*([A-Z]\d+)\s*[．.、：:]\s*(.+?)\s*$"),
)
SECTION_RE = re.compile(r"^#{1,3}\s+(\S.*?)\s*$")
INTENT_RE = re.compile(r"【面试官真实意图】\s*(.*)")
INTENT_LOOKAHEAD = 8

# 机器人 / 硬件专属词。软件岗与 AI 岗即使章节分流有偏差，命中这些词也要丢弃，
# 这是用户明确划定的禁区，兜底一层更稳。
ROBOT_TERMS = (
    "机器人",
    "整机",
    "CAN",
    "485",
    "ROS",
    "SLAM",
    "雷达",
    "激光",
    "工控机",
    "EVT",
    "DVT",
    "PVT",
    "老化",
    "固件",
    "OTA",
    "传感器",
    "AGV",
    "AMR",
    "电机",
    "舵机",
    "硬件",
    "示波器",
    "万用表",
    "电池",
    "充电",
)


class RoleRule:
    """岗位取题规则：从哪些文档取、取哪些章节、哪些词必须排除。"""

    def __init__(
        self,
        source_hints: tuple[str, ...],
        section_hints: tuple[str, ...] = (),
        block_terms: tuple[str, ...] = (),
        borrow_hints: tuple[str, ...] = (),
        borrow_labels: tuple[str, ...] = (),
        borrow_note: str = "",
    ) -> None:
        self.source_hints = source_hints
        self.section_hints = section_hints
        self.block_terms = block_terms
        self.borrow_hints = borrow_hints
        self.borrow_labels = frozenset(borrow_labels)
        self.borrow_note = borrow_note

    def accepts_source(self, title: str) -> bool:
        return any(hint in title for hint in self.source_hints)

    def borrows_from(self, title: str) -> bool:
        return bool(self.borrow_labels) and any(hint in title for hint in self.borrow_hints)

    def borrows_label(self, label: str) -> bool:
        return label in self.borrow_labels

    def accepts(self, item: dict) -> bool:
        if self.section_hints:
            section = item.get("section") or ""
            if not any(hint in section for hint in self.section_hints):
                return False
        if self.block_terms:
            blob = f"{item.get('question', '')} {item.get('intent', '')}"
            if any(term in blob for term in self.block_terms):
                return False
        return True


# 「软件+AI+管理流程 薄弱题库」里有一批题，正文参考答案本身就写在机器人语境下
# （AGV/AMR 整机、CAN 验证电机指令、一键回充、固件与软件版本适配、OTA 弱网）。
# 逐条核对正文后定向借给机器人岗，不做关键词泛匹配：那样会把 G2（提醒自己
# 纯软件岗别多提机器人的复盘条目）和 C1（压测服务器硬件）这类误命中一起带进来。
ROBOT_BORROWED_LABELS = (
    "D3",   # 弱网漏测：任务下发 / 状态上报 / OTA 升级的现场网络场景
    "D5",   # 版本发布复盘：固件与软件版本适配、工厂刷机流程
    "D11",  # 印象深刻的项目：整段为机器人项目复盘
    "E4",   # 软硬件测试分工与质量保障：软硬结合岗核心题
    "E6",   # 第三方 SDK / 成熟组件集成测试：外购组件为主的硬件产品同样适用
    "G8",   # 机器人项目只有 9 个月怎么快速上手
    "G9",   # 从纯软件转软硬结合最大的挑战
    "G11",  # 印象最深的 BUG：2cm 漂移的回充失败
)

# AI 段（A1~A9）只有 9 题，撑不满一场面试。逐条读过正文后，把其他分段里
# 真正围绕 AI 展开的题借过来：D10 整段在讲 AI 提效与业务的精力分配，
# E5 的参考答案里有本地大模型环境（vLLM + Docker Compose + GPU 监控），
# 都对得上他简历里的 AI 经历。
# 明确不借：G13 是七天复习计划表不是面试题；D8 的 AI 只是工具体系里的一个词；
# C2/C6/F2 的「模型」指压测模型与算法模型，与大模型无关。
AI_BORROWED_LABELS = (
    "D10",  # 业务测试与团队建设、AI 提效之间怎么分配精力
    "E5",   # 测试环境搭建：含本地大模型环境部署与 GPU 监控
)


ROLE_RULES: dict[str, RoleRule] = {
    # 硬件题库整本都是机器人实战真题，全量可用。
    "robot_hardware": RoleRule(
        source_hints=("硬件面试题", "机器人测试面试"),
        borrow_hints=("薄弱题库",),
        borrow_labels=ROBOT_BORROWED_LABELS,
        borrow_note="（软硬结合场景，可结合他的机器人项目提问）",
    ),
    # 薄弱题库里 B/C/E/F 段是纯软件题，D/G 段是流程与非技术题。
    "software_qa": RoleRule(
        source_hints=("薄弱题库",),
        section_hints=(
            "Python",
            "自动化",
            "性能",
            "压测",
            "管理",
            "流程",
            "通用测试",
            "纯软",
            "非技术",
        ),
        block_terms=ROBOT_TERMS,
    ),
    # A 段是大模型 / 智能体 / RAG 题，正好对应 AI 测试岗。
    "ai_qa": RoleRule(
        source_hints=("薄弱题库",),
        section_hints=("AI",),
        block_terms=ROBOT_TERMS,
        borrow_hints=("薄弱题库",),
        borrow_labels=AI_BORROWED_LABELS,
        borrow_note="（可结合他简历里的本地大模型部署与 AI 提效经历提问）",
    ),
}


def _clean(text: str) -> str:
    text = re.sub(r"（此前遗漏[^）]*）", "", text)
    text = re.sub(r"（🔴[^）]*）", "", text)
    return text.strip()


def _match_question(line: str) -> tuple[str, str] | None:
    for pattern in QUESTION_RES:
        match = pattern.match(line)
        if match:
            return match.group(1), match.group(2)
    return None


def extract_questions(text: str) -> list[dict]:
    """按编号抽题，附带所属章节与面试官意图。"""
    lines = [line.strip() for line in text.split("\n")]
    found: dict[str, dict] = {}
    section = ""
    for index, line in enumerate(lines):
        heading = SECTION_RE.match(line)
        if heading and not _match_question(line):
            section = heading.group(1)
            continue
        matched = _match_question(line)
        if not matched:
            continue
        label, body = matched
        if label in found:
            # 分块有重叠，同一题可能出现多次，保留首次命中。
            continue
        intent = ""
        for offset in range(index + 1, min(index + INTENT_LOOKAHEAD, len(lines))):
            hit = INTENT_RE.search(lines[offset])
            if hit:
                intent = hit.group(1).strip()
                break
        question = _clean(body)
        if len(question) < 4:
            continue
        found[label] = {
            "label": label,
            "question": question,
            "intent": intent[:120],
            "section": section,
        }
    return list(found.values())


def load_role_questions(role_key: str, limit: int = 40) -> list[dict]:
    """按岗位从知识库里取真题。知识库为空或没有匹配文档时返回空列表。"""
    rule = ROLE_RULES.get((role_key or "").strip())
    if not rule:
        return []

    collected: list[dict] = []
    borrowed: list[dict] = []
    for summary in knowledge_service.list_documents():
        title = summary.get("title") or ""
        is_own = rule.accepts_source(title)
        # AI 岗的借用题和它自己的题在同一份文档里，所以两种来源要能同时成立。
        can_borrow = rule.borrows_from(title)
        if not is_own and not can_borrow:
            continue
        document = knowledge_service.load_document(summary["doc_id"])
        if not document:
            continue
        text = "\n".join(document.get("chunks") or [])
        for item in extract_questions(text):
            if is_own and rule.accepts(item):
                item["source"] = title
                collected.append(item)
                continue
            # 借用题按人工核对过的编号白名单挑，不走章节与关键词规则。
            if can_borrow and rule.borrows_label(item["label"]):
                item["source"] = title
                item["borrowed"] = True
                if rule.borrow_note:
                    item["borrow_note"] = rule.borrow_note
                borrowed.append(item)

    # 借用题要预留固定名额，否则会被本岗位题库挤到末尾，再被摘要的条数上限截掉。
    if not borrowed:
        return collected[:limit]
    quota = min(len(borrowed), max(limit // 5, 1))
    return collected[: max(limit - quota, 0)] + borrowed[:quota]


def build_question_bank_brief(questions: list[dict], limit: int = 28) -> str:
    """渲染成面试官可直接取用的真题清单。"""
    if not questions:
        return ""
    # 借用题数量少且排在末尾，这里同样预留名额，避免被本岗位题库截断。
    borrowed = [item for item in questions if item.get("borrowed")]
    if borrowed and len(questions) > limit:
        own = [item for item in questions if not item.get("borrowed")]
        quota = min(len(borrowed), max(limit // 5, 1))
        questions = own[: max(limit - quota, 0)] + borrowed[:quota]
    lines = [
        "以下是候选人历次真实面试中被问到的原题，均来自他本人的复盘沉淀，具有实战价值。",
        "提问时优先从这些真题里挑选，并结合他的简历经历改写成具体场景，不要照读题号。",
        "",
    ]
    for item in questions[:limit]:
        entry = f"- {item['question']}"
        if item.get("intent"):
            entry += f"（考察点：{item['intent']}）"
        if item.get("borrowed"):
            entry += item.get("borrow_note") or ""
        lines.append(entry)
    return "\n".join(lines)
