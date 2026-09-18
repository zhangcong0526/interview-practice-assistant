"""意向岗位画像。

模拟面试按岗位切换考察重点：同一份简历，面机器人整机测试和面 AI 测试
该问的东西完全不同。这里固化四个岗位的边界、考察维度和检索关键词，
由 prompts 注入面试官上下文，并驱动知识库检索。
"""

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class RoleProfile:
    key: str
    name: str
    summary: str
    default_jd: str
    focus_areas: list[str] = field(default_factory=list)
    probe_points: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    resume_terms: list[str] = field(default_factory=list)


ROBOT_HARDWARE = RoleProfile(
    key="robot_hardware",
    name="机器人 / IoT 整机测试",
    summary=(
        "面向 AGV/AMR 物流机器人与智能硬件 IoT 产品的整机测试，"
        "结合候选人简历中的机器人项目实战经历，关注软硬边界、整机验证与分层定位。"
    ),
    default_jd=(
        "负责 AGV/AMR 机器人整机测试，覆盖 EVT/DVT/PVT 各阶段测试介入，"
        "组织功能、性能、专项（震动、老化、跌落）与现场验证；"
        "熟悉 CAN/485/TCP/MQTT 通信协议，能定位软硬件问题并分流给对应工程师；"
        "熟悉 ROS 生态、传感器融合、SLAM 建图与导航定位测试；"
        "负责固件 OTA 升级测试与版本兼容性验证。"
    ),
    focus_areas=[
        "优先结合候选人简历中的真实机器人项目：融合式智能物流机器人（顶升/夹抱等形态）、"
        "医院与物流配送中心场景、激光 SLAM 自主导航、大负载与大场地作业",
        "该场景的核心功能链路：建图与路网、导航避障、自动充电回充、仓门/顶升动作、"
        "鉴权、云端运单、门控梯控、多机协同、异常恢复",
        "多传感器融合：单线激光雷达+小激光雷达+深度相机+顶视相机的测试关注点与失效表现",
        "EVT/DVT/PVT 各阶段的测试介入点与准入准出标准",
        "软硬件边界判定：整机故障如何按物理层/固件层/软件层分层排查并分流",
        "通信协议实测：CAN 仲裁与终端电阻、485 与 CAN 的差异、协议选型依据",
        "ROS 相关测试：主从配置、rosbag 录制回放校验、topic 频率与丢包",
        "SLAM 建图与导航定位漂移的排查思路",
        "专项测试：老化、震动、跌落、弱网、低温高温的准入准出",
        "固件 OTA 升级测试与固件-软件版本兼容矩阵",
        "整机现场问题复现与批量故障处理",
        "IoT 智能硬件方向可延伸：4G/网络通信稳定性、云端任务下发与设备状态同步、"
        "RFID/二维码识别、设备离线与断网恢复",
    ],
    probe_points=[
        "机器人相关问题优先围绕候选人简历里做过的机器人项目提问（整机测试、"
        "EVT/DVT/PVT 流程建设、专项测试 SOP 等），不要泛泛问机器人行业常识",
        "医院场景要追问特殊约束：电梯交互、人流避障、夜间运行、消毒或洁净要求",
        "追问具体的准出标准数字，而不是停留在“测了”这个层面",
        "追问一次真实的硬件故障排查经历，看他能否讲清定位链路",
        "候选人若把硬件设计和硬件测试混为一谈，要追问边界认知",
    ],
    out_of_scope=[
        "不要深入大模型、RAG、AI 智能体的内部测试方法（这是 AI 测试岗的范畴）；"
        "但候选人在该公司用 LLM 做过用例生成和代码审计，可以作为工程效率话题问一两句",
        "不要把话题引到纯 Web/App/小程序的业务测试上",
    ],
    search_terms=[
        "整机测试 EVT DVT PVT 老化 专项",
        "CAN 485 通信协议 终端电阻 仲裁",
        "ROS rosbag SLAM 导航 定位 传感器",
        "OTA 固件升级 版本兼容",
        "AGV AMR 物流机器人 门控 梯控 多机协同 自动回充",
    ],
    resume_terms=[
        "机器人", "整机", "AGV", "AMR", "CAN", "485", "ROS", "SLAM", "雷达",
        "固件", "OTA", "电机", "回充", "充电", "硬件", "软硬", "工控机",
        "EVT", "DVT", "PVT", "老化", "传感器", "物联网", "IoT", "嵌入式",
        "底盘", "MCU", "烧录", "导航", "建图",
    ],
)

SOFTWARE_QA = RoleProfile(
    key="software_qa",
    name="软件测试（自动化 / 性能）",
    summary="面向 Web、App、小程序等业务系统的传统软件测试，覆盖用例设计、自动化框架与性能压测。",
    default_jd=(
        "负责 Web、App、小程序等业务系统的功能与非功能测试；"
        "设计并维护接口自动化与 UI 自动化框架，接入 CI/CD 持续集成；"
        "开展性能压测，定位系统瓶颈并推动优化；"
        "负责测试用例体系建设、测试计划与质量度量；"
        "熟悉缺陷管理流程与线上问题复盘。"
    ),
    focus_areas=[
        "测试用例设计方法：等价类、边界值、场景法、正交实验的实际取舍",
        "接口自动化框架分层设计、用例可维护性与断言策略",
        "UI 自动化的稳定性治理：元素定位、等待策略、失败重试与误报率",
        "CI/CD 集成：流水线卡点、冒烟集选取、失败阻断策略",
        "性能压测：压测模型设计、指标口径（TPS/RT/错误率）、瓶颈定位方法",
        "压测环境与生产环境的差异如何折算",
        "线上问题复盘与漏测分析、质量度量指标设计",
        "AI 在传统测试中的赋能场景：用例生成、缺陷归类、日志分析、代码评审辅助及其局限",
    ],
    probe_points=[
        "追问自动化的真实收益数字：覆盖率、执行耗时、漏测率变化",
        "追问性能瓶颈定位的完整链路，而不是只报一个 TPS 数字",
        "谈到 AI 赋能测试时，要追问准确率与人工兜底策略",
    ],
    out_of_scope=[
        "不要问机器人整机、硬件、CAN/485、ROS、SLAM 相关内容",
        "AI 话题只问“AI 如何赋能传统测试”，不深入大模型或智能体的内部测试方法",
    ],
    search_terms=[
        "测试用例设计 边界值 场景法 用例评审",
        "接口自动化 UI自动化 框架分层 CI CD",
        "性能测试 压测 TPS 响应时间 瓶颈定位",
        "缺陷管理 漏测 质量度量",
    ],
    resume_terms=[
        "接口自动化", "UI 自动化", "UI自动化", "自动化", "性能", "压测", "Locust",
        "JMeter", "Selenium", "Appium", "pytest", "CI/CD", "Jenkins", "流水线",
        "用例", "回归", "冒烟", "缺陷", "Web", "APP", "小程序", "客户端",
        "兼容", "适配", "安全", "渗透", "漏扫", "Docker", "环境",
    ],
)

AI_QA = RoleProfile(
    key="ai_qa",
    name="AI 测试（智能体方向）",
    summary="面向大模型应用与 AI 智能体的质量保障，关注幻觉治理、工具调用可靠性与效果评测体系。",
    default_jd=(
        "负责大模型应用与 AI 智能体（Agent）的质量保障体系建设；"
        "设计 RAG 检索质量、生成质量的评测方案与自动化评测流水线；"
        "开展幻觉识别与治理、提示词回归、模型版本升级的效果对比；"
        "测试智能体的工具调用（Function Calling / MCP）可靠性与多轮任务完成率；"
        "建设评测数据集、黄金标准集与人工标注流程。"
    ),
    focus_areas=[
        "大模型应用的测试策略：与传统软件测试在“确定性”上的本质差异",
        "RAG 系统测试：切块策略、召回率与准确率评估、rerank 效果验证",
        "AI 幻觉的识别与治理：提示词约束、上下文锚定、引用溯源、拒答机制",
        "AI 智能体测试：工具调用正确性、多轮任务完成率、失败回退与死循环检测",
        "Function Calling 与 MCP 的测试差异",
        "评测体系建设：黄金标准集构建、自动评测指标、LLM-as-judge 的可信度与偏差",
        "非确定性输出的回归测试：如何判定一次变更是改进还是劣化",
        "提示词版本管理与模型升级的兼容性验证",
        "AI 应用的安全测试：提示词注入、越权工具调用、敏感信息泄露",
    ],
    probe_points=[
        "追问评测数据集怎么来的、多大规模、如何保证代表性",
        "追问准确率数字的口径：谁judge的、样本量多少、如何复现",
        "候选人若把 AI 测试说成“试几个 case 看看效果”，要追问体系化方法",
        "简历里若有多段 AI 经历，提问要在它们之间轮换，不要一场面试只盯着同一个项目反复问",
        "AI 项目的量化数字往往缺口径，要逐个追问基线是多少、样本怎么取、谁来判定、怎么复现",
        "候选人一旦开始堆模型名、显卡型号、框架名这类技术名词，要立刻打断，"
        "追问它解决了什么业务痛点、省了多少时间、收益怎么量化",
        "本地大模型部署可以深挖选型理由：为什么自建而不是调用云端 API，"
        "量化方案对生成质量有无影响、怎么验证，显存与并发怎么规划",
        "MCP 或工具集成要追问实际打通了哪些工具、调用失败或返回脏数据时怎么处理",
        "AI 生成的测试用例要追问采纳率：生成了多少、实际用了多少、错的那部分怎么发现",
    ],
    out_of_scope=[
        "不要问机器人整机、硬件、CAN/485、ROS、SLAM、EVT/DVT/PVT 相关内容",
        "不要把主线放在 Web/App/小程序的传统功能测试上",
    ],
    search_terms=[
        "大模型 RAG 检索准确率 向量数据库 rerank",
        "AI智能体 Agent MCP function calling 工具调用",
        "AI幻觉 提示词 约束 评测 黄金标准集",
        "模型评测 回归 非确定性 LLM",
    ],
    resume_terms=[
        "AI", "大模型", "LLM", "RAG", "智能体", "Agent", "幻觉", "向量",
        "MCP", "提示词", "prompt", "vLLM", "Qwen", "DeepSeek", "GPT",
        "知识库", "语料", "标注", "推理", "算力", "GPU", "量化", "微调",
        "RPA", "Vibe Coding", "代码审计", "用例生成",
    ],
)

AI_APP_DEV = RoleProfile(
    key="ai_app_dev",
    name="AI 应用开发（Agent）",
    summary=(
        "面向 RAG、Agent 与大模型应用的工程设计、开发、部署和优化，"
        "关注上下文工程、工具调用、效果迭代、稳定性、安全与交付闭环。"
    ),
    default_jd=(
        "负责大模型应用与 AI Agent 的方案设计和工程落地；"
        "建设 Prompt、结构化输出、RAG 检索增强、Function Calling / MCP 工具调用、"
        "记忆与多步工作流；完成模型选型、上下文管理、badcase 分析、Trace 追踪和反馈迭代；"
        "保障应用低延迟、高可用与安全合规，并通过 Docker / Kubernetes、CI/CD、"
        "灰度发布、监控告警和降级回滚完成上线运维。"
    ),
    focus_areas=[
        "大模型基础工程：Token、上下文窗口、temperature、模型选型、成本与延迟权衡",
        "Prompt 工程与结构化输出：指令设计、JSON Schema、输出校验、重试和兜底策略",
        "RAG 应用开发：文档切块、Embedding、向量库、混合检索、rerank、引用溯源与知识库更新",
        "Agent 工程：任务规划、短期/长期记忆、Function Calling、MCP、Skill、工具权限和多智能体协作",
        "工作流可靠性：超时、重试、幂等、人工确认、死循环检测、越权防护与失败恢复",
        "上下文工程与效果迭代：Trace、badcase 沉淀、反馈闭环、提示词和检索策略的可观测回归",
        "性能与容量：P95/P99、QPS、缓存、模型路由、限流、熔断、异步推理和并发资源规划",
        "工程化交付：Docker、Kubernetes、CI/CD、灰度、回滚、监控、告警和降级方案",
        "安全合规：Prompt 注入防护、敏感信息脱敏、工具最小权限、输出审计与数据边界",
    ],
    probe_points=[
        "追问一个真实 Agent 或 RAG 功能从需求到上线的完整链路，而不是只停留在框架名词",
        "追问检索不准、模型幻觉、工具调用失败、上下文超长时分别如何定位和修复",
        "追问结构化输出如何保证可解析，解析失败或工具返回脏数据时如何兜底",
        "追问模型、向量库、Embedding、rerank、编排框架的选型依据和成本收益",
        "追问 Trace、日志、指标和 badcase 如何驱动下一轮迭代",
        "追问高并发或慢响应场景下的缓存、异步、限流、熔断和降级设计",
        "候选人若只堆 LangChain、MCP、多智能体等名词，要立刻追问实际业务痛点和落地细节",
    ],
    out_of_scope=[
        "不要问机器人整机、CAN/485、ROS、SLAM、EVT/DVT/PVT 等硬件整机测试内容",
        "不要把主线变成传统 Web/App/小程序的功能测试；传统软件只作为 AI 应用承载端讨论",
        "幻觉治理和评测只从开发落地、质量内建与线上反馈角度追问，不按 AI 测试岗的评测体系展开",
    ],
    search_terms=[
        "Token 上下文窗口 temperature 模型选型 Prompt JSON Schema 结构化输出",
        "RAG 文档切块 Embedding 向量数据库 混合检索 rerank 引用溯源",
        "Agent 规划 记忆 Function Calling MCP Skill 多智能体 工作流",
        "LLM应用 性能 P95 P99 QPS 缓存 模型路由 限流 熔断 异步推理",
        "Docker Kubernetes CI CD 灰度 回滚 监控 降级 Trace badcase 安全",
    ],
    resume_terms=[
        "AI", "大模型", "LLM", "RAG", "Agent", "智能体", "MCP", "Function Calling",
        "Prompt", "提示词", "Embedding", "向量数据库", "知识库", "工作流", "Skill",
        "DeepAgent", "vLLM", "Docker", "Kubernetes", "K8s", "CI/CD", "GPU", "模型路由",
        "Trace", "代码审计", "用例生成", "rerank", "上下文", "工具调用", "限流", "熔断",
    ],
)


ROLE_PROFILES: dict[str, RoleProfile] = {
    profile.key: profile for profile in (ROBOT_HARDWARE, SOFTWARE_QA, AI_QA, AI_APP_DEV)
}


def get_role(key: str) -> RoleProfile | None:
    return ROLE_PROFILES.get((key or "").strip())


def list_roles() -> list[dict]:
    return [
        {
            "key": profile.key,
            "name": profile.name,
            "summary": profile.summary,
            "default_jd": profile.default_jd,
            "focus_areas": profile.focus_areas,
            "search_query": build_search_query(profile),
        }
        for profile in ROLE_PROFILES.values()
    ]


def build_role_brief(profile: RoleProfile) -> str:
    """给面试官 prompt 用的岗位说明。"""
    lines = [f"目标岗位：{profile.name}", profile.summary, "", "本场面试的考察维度："]
    lines.extend(f"- {item}" for item in profile.focus_areas)
    if profile.probe_points:
        lines.append("")
        lines.append("追问重点：")
        lines.extend(f"- {item}" for item in profile.probe_points)
    if profile.out_of_scope:
        lines.append("")
        lines.append("本场面试的禁区（严格遵守）：")
        lines.extend(f"- {item}" for item in profile.out_of_scope)
    return "\n".join(lines)


def build_search_query(profile: RoleProfile, resume_hint: str = "") -> str:
    """知识库检索词：岗位关键词为主，简历技术栈为辅。"""
    query = " ".join(profile.search_terms)
    hint = (resume_hint or "").strip()
    if hint:
        query = f"{query} {hint[:200]}"
    return query


def _term_hits(term: str, blob: str) -> bool:
    """判断简历文本是否真的提到某个关键词。

    纯英文缩写要按词边界匹配，否则 "AI" 会命中 "IAM"、"GPU" 会命中拼接串，
    把无关项目当成岗位相关经历。中文词没有词边界问题，直接子串匹配。
    """
    if re.fullmatch(r"[A-Za-z0-9 .+/-]+", term):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", blob, re.I) is not None
    return term in blob


def _project_blob(project: dict) -> str:
    parts: list[str] = []
    for key in ("name", "role", "summary"):
        value = project.get(key)
        if value:
            parts.append(str(value))
    for key in ("tech_stack", "achievements", "metrics", "gaps"):
        values = project.get(key) or []
        parts.extend(str(item) for item in values if item)
    return " ".join(parts)


def build_resume_focus(profile: RoleProfile, resume_profile: dict, limit: int = 6) -> str:
    """从简历档案里挑出与本岗位相关的项目，生成定制化提问锚点。

    题库里的真题数量有限，且不可能覆盖简历中的每段经历。这里把岗位相关的
    项目、量化数字和待深挖点单独拎出来，让面试官既能用真题，也能就着简历
    现场出题。
    """
    if not resume_profile or not profile.resume_terms:
        return ""

    scored: list[tuple[int, dict]] = []
    for project in resume_profile.get("projects") or []:
        if not isinstance(project, dict):
            continue
        blob = _project_blob(project)
        if not blob:
            continue
        score = sum(1 for term in profile.resume_terms if _term_hits(term, blob))
        if score:
            scored.append((score, project))
    if not scored:
        return ""

    scored.sort(key=lambda pair: pair[0], reverse=True)
    lines = [
        "以下是候选人简历里与本岗位直接相关的经历，按相关度排序。",
        "题库真题覆盖不到的部分，请就着这些条目现场出题：点名具体项目、技术选型和数字追问，",
        "尤其是「待核实」列出的内容，那是简历里说得含糊、缺口径的地方。",
        "",
    ]
    seen: set[str] = set()
    for _, project in scored:
        name = str(project.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            # 简历里工作经历和项目经历会各抽一次，同名项目只保留一条。
            continue
        seen.add(key)
        period = str(project.get("period") or "").strip()
        lines.append(f"- {name}" + (f"（{period}）" if period else ""))
        summary = str(project.get("summary") or "").strip()
        if summary:
            lines.append(f"  做了什么：{summary[:160]}")
        for label, key_name in (("技术栈", "tech_stack"), ("量化结果", "metrics"), ("待核实", "gaps")):
            values = [str(v).strip() for v in (project.get(key_name) or []) if str(v).strip()]
            if values:
                lines.append(f"  {label}：" + "；".join(values)[:200])
        if len(seen) >= limit:
            break
    return "\n".join(lines)
