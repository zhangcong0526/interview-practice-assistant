# 面试练习助手

一个本地优先的面试备考工具：录音复盘、语音模拟面试、简历托管与优化建议、在线刷题、表达刻意练习五条线共用同一套本地知识库与简历档案。它用于考前练习和复盘，不提供任何规避面试检测的功能。

## 功能

- 录音或上传 WAV / MP3 / M4A / WebM / MP4 等音视频文件
- 超大文件分片上传，前端按 8MB 逐片发送，单个分片失败自动重试
- 默认支持 2GB 单文件，超过 100MB 录音可直接上传
- FFmpeg 自动压缩为 16kHz 单声道 24kbps Opus
- 超过 Whisper 24MB 安全线时自动按 600 秒切片并拼接时间戳
- 逐题评估相关性、结构、具体性、量化、简洁五个维度
- 生成整体优势、短板、参考回答和模拟面试官追问
- 支持已有转写文本粘贴模式
- JD 和简历内容保存在浏览器 localStorage
- 支持本地知识库文档导入并在分析时自动检索相关片段
- 支持飞书、腾讯等公开 HTTPS 文档链接，以及受限文档的粘贴正文导入
- 本地文档支持 TXT / Markdown / PDF / DOCX / PPTX / XLSX / CSV / JSON / HTML
- 语音模拟面试：AI 面试官逐轮提问，你用麦克风回答，结束后自动生成评分报告；内置四个岗位方向（机器人/IoT 整机测试、软件测试、AI/智能体测试、AI 应用开发/Agent），提问边界和真题库按岗位隔离
- 语音流式播报：后端边合成边下发，起播约 1 秒；切换岗位时旧播报按世代号作废，不会出现声音混杂或开头被截断
- 简历托管：导入简历后自动抽取项目、量化数据和待深挖点，提问与评分都以简历为基础展开；长简历自动分段解析、断点续传
- 简历优化建议：把真实面试题库的高频考点、刷题掌握度、简历风险点三者交叉，按优先级给出改写示范，缺数据一律用「待补充」占位，不替用户编造
- 表达训练：一次一题的口述刻意练习，支持「看答案照读 → 关键词串联 → 无提示实战」三档循序渐进；自动统计语速、语气词、口头禅、卡顿重复和结构信号，给出流畅度/结构化/笃定感三项分数、下一训练档建议和连续打卡趋势
- 在线考题：勾选知识点关键词，从知识库生成单选/多选/判断题，交卷立即判分并给出复习指引
- 错题本与掌握度追踪：错题自动归档，按知识点统计正确率，判断能否进入下一板块
- 知识库防重复：按正文内容判重，同一份资料换文件名再传会被拦下并提示已有文档名

## 环境要求

- Git
- Node.js 20+
- Python 3.11+
- FFmpeg / FFprobe 已加入 PATH
- DeepSeek / 火山引擎 Ark / MiniMax API Key 之一用于 LLM 分析、命题和面试点评
- 语音转写默认本地运行，不需要额外 API Key

Windows 用户可先安装依赖，再使用仓库根目录的一键启动脚本：

```powershell
winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.11
winget install Gyan.FFmpeg
```

安装后请重新打开命令行窗口，让 PATH 生效。

## 快速开始（Windows）

1. 克隆仓库：

   ```powershell
   git clone https://github.com/zhangcong0526/interview-practice-assistant.git
   cd interview-practice-assistant
   ```

2. 双击仓库根目录的 `start.bat`。

首次启动会自动完成：

- 创建 `backend/.venv` Python 虚拟环境；
- 安装后端依赖和前端依赖；
- 从 `backend/.env.example` 复制生成 `backend/.env`；
- 启动后端 `http://127.0.0.1:8000`；
- 启动前端并打开 `http://127.0.0.1:5173/`。

首次生成 `backend/.env` 后，按脚本提示填写：

```ini
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的 DeepSeek Key
ASR_BACKEND=local
ASR_MODEL=auto
ASR_DEVICE=auto
MAX_UPLOAD_SIZE_MB=2048
UPLOAD_CHUNK_MB=8
```

如果首次启动时跳过填写，页面仍可打开，但 AI 分析、在线命题、模拟面试点评会不可用。也可以在页面右上角打开「模型配置」，在线切换 DeepSeek、火山引擎 Ark、MiniMax 或 OpenAI 兼容服务，填写 API Key、Base URL 和模型名，并点击「测试连接」做一次轻量真实调用。Key 只写入本机 `backend/.env`，界面只显示掩码；保存后立即生效，不需要重启服务。火山引擎 Ark 的模型值可填官方 Model ID 或你创建的接入点 ID。如果继续手工编辑文件，补存 `backend/.env` 后双击 `stop.bat` 停止服务，再重新运行 `start.bat`。

使用期间任务栏中最小化的「面试助手-后端」和「面试助手-前端」窗口不要关闭。用完后双击根目录 `stop.bat`，脚本会停止 8000 和 5173 端口上的服务。

### 创建桌面快捷方式

建议右键仓库根目录的 `start.bat` 和 `stop.bat`，选择「发送到」→「桌面快捷方式」。不要直接把 `.bat` 复制到桌面，因为根目录脚本需要通过相对路径调用 `scripts/` 目录中的通用启动逻辑。

### 依赖更新

启动脚本会根据 `backend/requirements.txt` 和 `frontend/package-lock.json` 的哈希判断是否需要重新安装依赖。正常 `git pull` 后直接运行 `start.bat` 即可；手动排查时也可以执行：

```powershell
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd frontend
npm ci
```

## 手动部署

### Windows PowerShell

启动后端：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开一个 PowerShell 窗口启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

### macOS / Linux

启动后端：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，按所选厂商填写对应 API Key
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开一个终端启动前端：

```bash
cd frontend
npm ci
npm run dev
```

手动部署时同样访问 `http://127.0.0.1:5173/`。开发服务器已将 `/api` 代理到 `http://127.0.0.1:8000`。

`OPENAI_API_KEY` 只有把 `ASR_BACKEND` 改成 `openai` 时才需要，留空不影响本地转写。

## 大文件说明

浏览器端不会把整个文件读入内存，而是使用 `File.slice()` 逐片上传。后端收到分片后落盘，全部完成后校验总字节数并合并。

如果通过 Nginx 反向代理部署，需要同步放宽请求体限制：

```nginx
client_max_body_size 16m;
```

这里只需要大于等于单个分片大小；若改用 `UPLOAD_CHUNK_MB`，请同步调整该值。

## 语音转写说明

录音转写默认在本地完成，使用 `faster-whisper`，**不需要 OpenAI API Key，也不产生调用费用**。

- 优先使用 NVIDIA GPU（`float16`），不可用时自动回退 CPU（`int8`），不会因为缺 CUDA 库而中断。
- 模型按设备自动选择：GPU 用 `medium`（听写"服务端/自动化"这类术语更准），CPU 用 `small`（速度优先）。
- 首次使用会自动下载模型到 `C:\Users\<用户名>\.cache\huggingface\`，`medium` 约 770MB，请预留磁盘与下载时间。
- 实测速度：RTX 4050 + `medium` 约为音频时长的 3.8 倍速，40 分钟录音约 10 分钟转完；CPU 约 2 倍速。
- 想做纯离线部署或换更小的模型，改 `backend/.env` 里的 `ASR_MODEL` / `ASR_DEVICE` 即可。
- 仍想走云端：把 `ASR_BACKEND` 设为 `openai` 并配置 `OPENAI_API_KEY`。

## 知识库说明

本地知识库文档会先上传并解析，再按每段约 900 字切分。开始分析时，后端会根据 JD、简历和转写文本做中文 BM25 检索，把最相关的片段加入分析上下文。

- 本地文档：直接在“测试知识库”面板选择文件，复用 8MB 分片上传。
- 飞书文档 / 腾讯文档：公开可访问的 HTTPS 链接可以尝试直接解析；需要登录或动态渲染的页面，请复制正文后使用“粘贴正文”导入。
- 数据位置：解析后的知识库 JSON 保存在 `backend/data/knowledge/documents/`。

## 语音模拟面试说明

在“面试材料”里切换到“模拟面试”即可开始。AI 面试官会结合 JD、简历和知识库逐轮提问，一次只问一个问题；当回答含糊或缺少量化结果时会就该点追问，回答跑题时会把话题拉回来。面试结束后，整段对话会直接进入评分流程，生成与录音复盘一致的报告。

- 面试官语音使用微软 Edge 神经网络音色（`edge-tts`），可在开始前切换音色和语速；比浏览器自带语音自然得多，同样不产生 API 费用。合成结果按内容缓存，重复句子不会重复请求。
- 若神经网络语音不可用（离线、网络异常），会自动回退到浏览器 `speechSynthesis`，界面上会显示"备用音色"标记，面试不会中断。
- 模拟面试的实时识别继续使用浏览器 `SpeechRecognition`，优先保证对话低延迟。
- 表达训练不走浏览器实时识别：录音结束后整段送本地 Whisper 转写，避免浏览器把“嗯、呃、那个”等语气词过滤掉或把口语润色成书面语。
- 识别功能目前只在 Chrome 和 Edge 上可用。其他浏览器会自动切换为打字回答，流程不受影响。
- 面试官朗读期间会暂停麦克风，避免把合成语音识别成你的回答。
- 建议佩戴耳机，进一步减少外放声音被麦克风拾取的干扰。

## 简历托管说明

在右侧“简历档案”面板上传简历文件或粘贴简历正文，后端会调用 LLM 抽取成结构化档案，包含姓名、年限、技能、项目（角色、周期、技术栈、成果、量化数据、待深挖点）、工作经历、教育、亮点和风险点。

模拟面试和评分分析都会自动注入当前激活的简历档案。面试官被要求点名简历里的具体项目和数字提问，优先追问抽取出的待深挖点和风险点，并且不得编造简历中没有的经历。

- 支持格式：PDF / DOCX / TXT / Markdown / HTML，单份上限 20MB。
- 可以保存多份简历，针对不同岗位切换；“启用”后的档案即为当前面试依据。
- 数据位置：`backend/data/resume/profiles/`，激活指针为 `backend/data/resume/active.json`。
- 扫描版 PDF 提取不到文字时会明确报错，请改用粘贴正文。
- 侧栏的“补充说明”用于简历之外的临时信息，不会替代简历档案。

### 长简历分段解析

简历越长，模型需要输出的结构化内容越多，一次性解析容易撞上输出长度上限导致 JSON 被截断。超过 6000 字的简历会自动走分段流程：

1. 按简历的自然板块切分（专业技能、工作经历、项目一/项目二、教育背景等），切分点落在标题上，一个项目的描述不会被拦腰截断。
2. 每段单独解析，结果实时写入 `backend/data/resume/parsing/{job_id}.json`。
3. 全部完成后合并：同名项目按名称去重并补齐字段，技能、亮点、风险点按顺序去重合并。
4. 合并后再做一次全局收尾，补上 headline 和只有通读全文才能发现的风险点（例如“技能里写了 Kubernetes 但项目中没有对应实践”“两段经历之间有一个月空档”）。

断点续传：`job_id` 由简历正文的哈希决定。某一段因为网络或模型问题失败时，已完成的段会保留在进度文件里，档案照常生成但会在风险点中标注缺口；用同一份简历重新上传即可续跑，已解析的部分不会重复请求模型。全部段落成功后进度文件自动清除。

## 在线考题说明

顶部切换到「在线刷题」即可使用。这条流程是为「把面试错题本变成可反复检验的题库」设计的：

1. 导入资料。把面试错题本、面试纪要、技术总结导入知识库（本地文档、公开链接或粘贴正文都可以）。
2. 勾选知识点。系统会用 LLM 从资料中提炼关键词并按板块归类（接口测试、性能测试、数据库等），支持搜索过滤、整组勾选，也可以先限定只从某几份文档里出题。
3. 生成试卷。自由设置单选、多选、判断的题量和难度。命题严格基于检索到的资料原文，并会优先覆盖你历史上的薄弱知识点，同时避开最近考过的题目。
4. 交卷判分。客观题在后端本地精确比对判分，不依赖模型，成绩可复现。多选题必须完全选对才算对。
5. 复习指引。LLM 结合你选错的具体选项推断误区，输出每个薄弱知识点的诊断、要补的内容、怎么练，以及有先后顺序的复习步骤。

### 能不能进入下一板块

成绩单会明确给出结论。放行条件是本次正确率达到 85% 以上，且没有知识点在历史上反复出错。这个门槛在后端强制校验，模型判断再宽松也不会越过。

### 错题本

答错的题目自动进入错题本，记录你的错误答案、正确答案和解析。同一道题连续答对两次会自动移出。侧栏按知识点展示累计正确率和掌握等级，可以随时点「错题重练」针对薄弱知识点重新组卷。

### 数据位置

- 试卷：`backend/data/quiz/papers/`
- 答卷：`backend/data/quiz/attempts/`
- 错题本：`backend/data/quiz/mistakes.json`
- 掌握度：`backend/data/quiz/mastery.json`
- 关键词缓存：`backend/data/quiz/topics.json`（资料变化后会自动失效，也可以手动点「重新提炼」）

试卷中的正确答案和解析只保存在后端，答题接口返回的题目不含答案字段，交卷后才随成绩一起下发。

## 表达训练说明

顶部切换到「表达训练」。它针对的是「脑子里有货但说不出来、紧张、口头禅多」：一次只练一道真题，口述 60 到 90 秒后提交，系统从转写文本中本地计算客观指标（语速、语气词、口头禅、卡顿重复、结构信号词），打出流畅度、结构化、笃定感三项分数；LLM 只负责给出可执行的表达改法、一句话改写示范、下一轮练习重点和临场心态建议，不评价技术答案对错。

训练分三档，建议同一道题按顺序推进：

1. **看答案照读**：完整展示题库原文参考答案，先练停顿、节奏和整句稳定输出。
2. **关键词串联**：只展示从参考答案和薄弱点提醒中提取的关键词，自己组织结构并完整表达。
3. **无提示实战**：不展示答案和关键词，按真实面试状态压测。

系统会把训练档位和晋级结果持久化到本地：照读不通过会建议同题重读，关键词通过后进入无提示；无提示不通过会退回关键词，通过后再换新题。趋势统计会单独展示无提示模式的题数和均分，避免把照读分数误判成真实掌握度。

- 练习题按所选岗位直接取自真实面试题库，并避开最近练过的题。
- 每天目标 3 题，记录连续打卡天数和最近 10 次的流畅度趋势，用数据验证是否真的在进步。
- 数据位置：`backend/data/expression/sessions/`。

## 简历优化建议说明

简历档案面板点「生成建议」。输入只来自系统已有数据：两份真实面试题库（按章节统计高频方向）、在线刷题中正确率低于 80% 的知识点、简历解析时标注的缺口与风险点。输出按优先级排序，每条都附真题目号或刷题数据作为依据。

- 硬性规则：改写示范只能重组简历已有内容，缺失的数字一律输出为【待补充：缺什么】，绝不编造。
- 数据位置：`backend/data/resume/advice/`，重新导入简历后缓存自动失效。

## 常见问题

### 双击 `start.bat` 后提示找不到 Python

安装 Python 3.11+，安装时勾选 Add python.exe to PATH，然后重新打开窗口再运行脚本。脚本也支持 Windows 的 `py -3` 启动器，但仍需要先安装 Python。

### 提示找不到 Node.js 或 npm

安装 Node.js 20+，安装后重新打开命令行窗口。可用以下命令确认：

```powershell
node --version
npm --version
```

### 提示找不到 ffmpeg / ffprobe

FFmpeg 用于音视频压缩、元数据读取和本地 Whisper 转写。Windows 可用 `winget install Gyan.FFmpeg` 安装，安装后重新打开终端，确认：

```powershell
ffmpeg -version
ffprobe -version
```

缺少 FFmpeg 时页面仍能启动，但录音/视频相关能力会受限。

### 页面能打开，但 AI 功能不可用

检查 `backend/.env` 中当前启用厂商对应的 API Key 是否已填写，或在页面右上角「模型配置」中补填并测试连接。保存后立即生效。本地 Whisper 转写不依赖这个 Key，但 LLM 分析、命题、点评需要它。

### 5173 或 8000 端口被占用

`start.bat` 会清理这两个固定端口上的旧进程。如果其他重要软件正在使用这些端口，请先关闭它，或先运行 `stop.bat`。前端固定入口是 `http://127.0.0.1:5173/`，后端固定为 `http://127.0.0.1:8000`。

### 第一次转写等了很久

首次使用本地 Whisper 会下载模型到当前用户的 Hugging Face 缓存目录。网络较慢时只需等待首次下载完成，后续会复用本地缓存。

### 更新代码后依赖报错

启动脚本会依据依赖锁文件自动安装更新。仍失败时，在仓库根目录手动执行：

```powershell
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd frontend
npm ci
```

## 整体架构

```
frontend/   React + TypeScript + Vite，纯前端 SPA
backend/    FastAPI + uvicorn
  app/
    routers/    HTTP 接口层：录音分析、模拟面试、知识库、题库、TTS、简历、表达训练、模型配置
    services/   业务层：asr(faster-whisper) / tts(edge-tts) / llm(DeepSeek / Ark / MiniMax / OpenAI) /
                knowledge(BM25 检索) / feishu(无头浏览器分页抓取) / question_bank /
                quiz(本地判分与掌握度) / resume / resume_advisor / expression
    prompts.py  所有 LLM 提示词集中管理
    roles.py    四个岗位的考察维度、禁区和真题库取用规则
    data/       全部本地数据（知识库、简历、试卷、练习记录、TTS 缓存），不入库
```

设计原则：凡是能本地确定性计算的（判分、表达指标、检索、去重）都不经过 LLM；模型只负责命题、点评、解析这类需要理解的环节，因此结果可复现、失败可降级。

## 隐私与数据

所有个人数据（简历、录音转写、题库文档、练习记录）只保存在本机 `backend/data/` 下，该目录已被 `.gitignore` 忽略，不会进入仓库。API Key 只通过 `backend/.env` 配置（仓库里只有不含 Key 的 `.env.example`），代码中没有任何硬编码密钥。

对外网络请求只有三类：LLM 调用（DeepSeek/OpenAI）、Edge TTS 在线合成语音、导入公开链接时抓取网页内容；本地 whisper 转写完全离线。需要注意：`edge-tts` 依赖的是微软 Edge 的非公开朗读接口，二次开发或商用前请自行评估其使用条款。

## 开源声明

本仓库以 MIT 协议开源，见 [LICENSE](LICENSE)。仓库不包含任何个人简历、面试题库或练习数据，首次使用需要自行导入。
