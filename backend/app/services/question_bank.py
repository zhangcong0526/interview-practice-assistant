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
QUESTION_HEADER_RE = re.compile(
    r"^#{0,6}\s*(Q\d+|[A-Z]\d+)\s*[：:．.、]\s*(.+?)\s*$",
    re.M,
)
SECTION_RE = re.compile(r"^#{1,3}\s+(\S.*?)\s*$", re.M)
INTENT_RE = re.compile(r"【面试官真实意图】\s*(.*)")
INTENT_LOOKAHEAD = 8
# AI 应用开发学习笔记使用连续的「问：... 答：...」，不是 Q1/A1 编号格式。
INLINE_QA_RE = re.compile(
    r"(?:^|\n)\s*(?:下一题)?问[：:]\s*(.*?)\s*答[：:]\s*(.*?)"
    r"(?=\n\s*(?:下一题)?问[：:]|\n#{1,6}\s+|$)",
    re.S | re.M,
)

ANSWER_MARKERS = (
    "【参考答案（可直接说出口）】",
    "【参考答案】",
    "[参考答案]",
    "参考答案：",
    "参考答案:",
)
WEAK_MARKERS = ("【你的薄弱点提醒】", "【薄弱点提醒】", "[薄弱点提醒]")
STOP_MARKERS = (
    "【薄弱点提醒】",
    "【快速补知识点攻略】",
    "【面试官真实意图】",
    "【你的薄弱表现】",
    "【你的回答】",
    "【拓展追问】",
)

DOMAIN_TERMS = (
    "CAN", "485", "TCP", "UDP", "MQTT", "QoS", "keepalive", "LWT",
    "遗嘱消息", "自动重连", "心跳", "ROS", "SLAM", "BMS", "OTA",
    "EVT", "DVT", "PVT", "终端电阻", "报文ID", "仲裁", "波特率", "传感器",
    "电机", "电池", "工控机", "激光雷达", "摄像头", "导航", "回充", "固件",
    "整机", "老化", "示波器", "万用表", "物联网", "急停", "安全机制",
    "硬件直连", "驱动器", "接触器", "制动距离", "手动复位", "防回滚",
    "烧录", "J-Link", "ST-Link", "SWD",
    "需求评审", "测试计划", "测试方案", "测试用例", "测试点", "等价类",
    "边界值", "判定表", "因果图", "场景法", "异常场景", "正向流程",
    "接口测试", "功能测试", "性能测试", "压力测试", "负载测试",
    "稳定性测试", "并发", "容量", "响应时间", "吞吐量", "资源利用率",
    "自动化测试", "UI自动化", "接口自动化", "回归测试", "冒烟测试",
    "验收测试", "缺陷", "日志", "监控", "抓包", "Fiddler", "Charles",
    "Postman", "JMeter", "LoadRunner", "Selenium", "Appium", "小程序",
    "Wireshark", "WebSocket", "Master-Slave", "/etc/hosts",
    "ROS_MASTER_URI", "rostopic", "rosbag", "RVIZ", "Foxglove",
    "APP", "Web", "兼容性测试", "弱网测试", "中断测试", "权限测试",
    "数据库", "MySQL", "Redis", "Linux", "SQL", "Python", "Java",
    "Docker", "Kubernetes", "Git", "GitLab", "CI/CD", "Jenkins",
    "大模型", "LLM", "智能体", "Agent", "RAG", "向量数据库", "Milvus",
    "Embedding", "召回", "重排序", "rerank", "Prompt", "提示词", "MCP", "工具调用",
    "Function Calling", "Skill", "幻觉", "上下文", "上下文工程", "知识库", "模型评测",
    "数据集", "准确率", "badcase", "模型路由", "Trace", "限流", "熔断", "K8s",
    "数据脱敏", "Prompt注入", "FAT", "SAT", "提测准入", "环境变更",
    "环境配置", "版本管理",
)

GENERIC_TERMS = (
    "测试报告", "评审", "评审机制", "checklist", "模板", "流程", "风险",
    "覆盖率", "事实", "书面记录", "配置", "稳定性", "硬件", "长期深耕", "离职原因",
    "缺点", "失败项目", "上级", "加班",
)

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
        parse_inline_qa: bool = False,
    ) -> None:
        self.source_hints = source_hints
        self.section_hints = section_hints
        self.block_terms = block_terms
        self.borrow_hints = borrow_hints
        self.borrow_labels = frozenset(borrow_labels)
        self.borrow_note = borrow_note
        self.parse_inline_qa = parse_inline_qa

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
    # AI 应用开发资料是连续问答笔记，按行内「问/答」结构解析；旧题库仍走编号解析。
    "ai_app_dev": RoleRule(
        source_hints=("AI应用开发", "大模型应用开发", "Agent开发"),
        block_terms=ROBOT_TERMS,
        parse_inline_qa=True,
    ),
}


def _clean(text: str) -> str:
    text = re.sub(r"（此前遗漏[^）]*）", "", text)
    text = re.sub(r"（🔴[^）]*）", "", text)
    return text.strip()


def _merge_overlapping_chunks(chunks: list[str]) -> str:
    """把带 120 字重叠的知识切块尽量还原成原文，避免答案跨块重复或缺半句。"""
    merged = ""
    for chunk in chunks:
        chunk = (chunk or "").strip()
        if not chunk:
            continue
        if not merged:
            merged = chunk
            continue

        overlap = 0
        upper = min(180, len(merged), len(chunk))
        for size in range(upper, 40, -1):
            if merged.endswith(chunk[:size]):
                overlap = size
                break
        merged += chunk[overlap:] if overlap else f"\n{chunk}"
    return merged


def _marker_content(block: str, markers: tuple[str, ...], stop_markers: tuple[str, ...]) -> str:
    starts = [(pos, marker) for marker in markers if (pos := block.find(marker)) >= 0]
    if not starts:
        return ""
    start, marker = min(starts, key=lambda item: item[0])
    rest = block[start + len(marker) :]
    end_positions = [pos for pos in (rest.find(marker) for marker in stop_markers) if pos >= 0]
    separator = re.search(r"\n\s*(?:---|\*\*\*|#{1,6}\s+)", rest)
    if separator:
        end_positions.append(separator.start())
    if end_positions:
        rest = rest[: min(end_positions)]
    return rest.strip().strip('"“”').strip()


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = re.sub(r"\s+", "", item).strip("：:；;，,。.!！?？")
        if 1 < len(cleaned) <= 24 and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _extract_keywords(answer: str, weak_tip: str, question: str) -> list[str]:
    raw_explicit: list[str] = []
    for hit in re.finditer(r"关键(?:词)?[：:]\s*([^\n。]*)", weak_tip):
        raw_explicit.extend(
            # 斜杠可能是路径或协议名的一部分（/etc/hosts、TCP/IP），不能作为分隔符。
            re.split(r"[、,，；;｜|和及=＝\s]+|——|--|->|→", hit.group(1))
        )
    context = f"{question}\n{answer}"
    explicit = [item for item in _unique(raw_explicit) if item in context]

    cue_blacklist = {
        "标准方案",
        "测试重点",
        "硬件实现",
        "为什么这么设计",
        "测试时怎么测",
        "注意事项",
        "一句话",
        "第一步",
        "第二步",
        "第三步",
    }

    def cue_phrases() -> list[str]:
        phrases: list[str] = []
        cue_pattern = re.compile(
            r"(?:^|[\n|；;。]|[①②③④⑤⑥⑦⑧⑨⑩])\s*[①②③④⑤⑥⑦⑧⑨⑩]?\s*([^|\n：:；;。]{2,24}?)\s*——"
        )
        for match in cue_pattern.finditer(answer):
            phrase = match.group(1).strip()
            phrase = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩]\s*", "", phrase)
            phrase = re.sub(r"^(?:适合|用于|包括|通过|按照|基于|需要|必须|就是|是)", "", phrase)
            if phrase not in cue_blacklist:
                phrases.append(phrase)

        for match in re.finditer(r"[‘'“\"]([^’'”\"]{2,28})[’'”\"]", answer):
            prefix = answer[max(0, match.start() - 20) : match.start()]
            # 只把「一句话/核心」后的总结性引号拆成线索，避免把口播示例里的整句话当成关键词。
            if not any(marker in prefix for marker in ("一句话", "核心是", "必须说出", "核心")):
                continue
            for part in re.split(r"[、，,；;]\s*", match.group(1)):
                part = part.strip()
                if 2 <= len(part) <= 14 and not re.search(r"[。！？!?]", part):
                    phrases.append(part)

        for match in re.finditer(
            r"第[一二三四五六七八九十0-9]+步：\s*([^，,。；;：:\n|—–-]{2,20})",
            answer,
        ):
            phrase = match.group(1).strip()
            if not phrase.startswith("不要"):
                phrases.append(phrase)
        return [item for item in _unique(phrases) if item in answer]

    def present(terms: tuple[str, ...]) -> list[str]:
        found = []
        for term in terms:
            position = answer.find(term)
            if position < 0 and term in question:
                position = len(answer) + question.find(term)
            if position >= 0:
                found.append((position, term))
        return [term for _, term in sorted(found, key=lambda item: item[0])]

    terms = present(DOMAIN_TERMS)[:6]
    if len(terms) < 4:
        terms.extend(term for term in present(GENERIC_TERMS) if term not in terms)

    keywords: list[str] = []

    def add_keyword(keyword: str) -> None:
        keyword = keyword.strip()
        if not keyword or any(keyword == existing or keyword in existing or existing in keyword for existing in keywords):
            return
        keywords.append(keyword)

    for keyword in explicit:
        add_keyword(keyword)
    for keyword in cue_phrases():
        add_keyword(keyword)
    for keyword in terms:
        add_keyword(keyword)

    return _unique(keywords)[:8]


def extract_questions(text: str, include_inline: bool = False) -> list[dict]:
    """按编号抽题，附带章节、意图、参考答案和原文关键词。"""
    matches = list(QUESTION_HEADER_RE.finditer(text or ""))
    headings = [
        (match.start(), match.group(1))
        for match in SECTION_RE.finditer(text or "")
        if not QUESTION_HEADER_RE.match(match.group(0).strip())
    ]
    found: dict[str, dict] = {}

    for index, match in enumerate(matches):
        label, body = match.group(1), match.group(2)
        if label in found:
            continue
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start() : block_end]
        section = ""
        before = [heading for pos, heading in headings if pos < match.start()]
        if before:
            section = before[-1]

        answer = _marker_content(block, ANSWER_MARKERS, STOP_MARKERS)[:1800]
        weak_tip = _marker_content(block, WEAK_MARKERS, STOP_MARKERS)[:600]
        intent = _marker_content(
            block,
            ("【面试官真实意图】", "[面试官真实意图]"),
            ANSWER_MARKERS + WEAK_MARKERS + STOP_MARKERS,
        )[:120]
        question = _clean(body)
        if len(question) < 4:
            continue
        found[label] = {
            "label": label,
            "question": question,
            "intent": intent,
            "section": section,
            "reference_answer": answer,
            "weak_tip": weak_tip,
            "keywords": _extract_keywords(answer, weak_tip, question),
        }

    items = list(found.values())
    if not include_inline:
        return items

    seen_questions = {
        re.sub(r"\s+", "", item["question"]).strip("：:；;，,。.!！?？")
        for item in items
    }
    inline_items: list[dict] = []
    for index, match in enumerate(INLINE_QA_RE.finditer(text or ""), start=1):
        question = _clean(re.sub(r"\s+", " ", match.group(1)).strip())
        answer = re.sub(r"\n{3,}", "\n\n", match.group(2)).strip().strip('"“”').strip()
        normalized = re.sub(r"\s+", "", question).strip("：:；;，,。.!！?？")
        if len(question) < 4 or len(answer) < 10 or normalized in seen_questions:
            continue
        before = [heading for pos, heading in headings if pos < match.start()]
        seen_questions.add(normalized)
        inline_items.append(
            {
                "label": f"AD{index}",
                "question": question,
                "intent": "",
                "section": before[-1] if before else "",
                "reference_answer": answer[:1800],
                "weak_tip": "",
                "keywords": _extract_keywords(answer[:1800], "", question),
            }
        )
    return items + inline_items


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
        text = _merge_overlapping_chunks(document.get("chunks") or [])
        for item in extract_questions(text, include_inline=rule.parse_inline_qa):
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
