# 求职工作台

个人自用的求职工作台：把岗位 JD、简历、项目源码与模拟面试串成一条可追溯的流水线。当前完成 **M0–M6**。

它不是一个"接了大模型的 CRUD 后台"。所有智能能力都跑在同一套自研 **agent harness** 上：每次执行都是一个可回放、可取消、可审计的 Run——模型轮次、工具调用、上下文装配、提示词版本、token 与事件全部落库。业务代码只写 handler，不直接调模型、不自己管状态。

## 技术栈

- 前端：Vue 3 + TypeScript + Vite + Naive UI + Pinia
- 后端：FastAPI + SQLAlchemy 2.0 async + Pydantic v2 + 自研 agent harness
- 任务：Celery + Redis（Redis 跑 Docker，API 与 worker 都跑 Windows 原生进程）
- 存储：SQLite（WAL）+ 本地文件
- 模型：OpenAI 兼容 Chat Completions（`openai` SDK），默认阿里云百炼 `qwen3.8-flash`

## Harness：一个执行入口，六道边界

```
前端 / HTTP
   │  POST /api/runs  ·  POST /api/chat/sessions/{id}/turns
   ▼
FastAPI ──enqueue──► Celery（celery 队列＝业务 agent，chat 队列＝对话轮次）
                          │
                          ▼
                   execute_run()          ← 唯一执行入口，状态迁移只在这里发生
                          │
                    RunExecutor / RunHandle
            ┌─────────────┼──────────────┬──────────────┐
            ▼             ▼              ▼              ▼
        LLMClient    ToolRegistry   ContextBudget   Prompt(版本化)
            │             │              │              │
            └────────► run_steps ──► run_events ──► SQLite
                                                      │
                                               Redis pub/sub
                                                      │
                                             SSE（Last-Event-ID 补历史）
```

| 边界 | 做法 |
|---|---|
| **状态** | `runs` 状态机在库里；worker 重启把遗留 `running` 标为 `interrupted`，不假装续跑 |
| **模型** | 业务代码只能调 `RunHandle.call_model()` / `.step()`，不能直接 new 客户端 |
| **工具** | 全部注册进 `ToolRegistry`；入参 Pydantic 校验、超时、路径白名单、风险分级都在这一层 |
| **上下文** | `ContextBudget` 显式估算 token、按优先级装入、截断或丢弃并记录原因 |
| **预算** | 每次调用累加 token，超 `budget_tokens` 抛 `BudgetExceeded`；取消标志逐轮检查 |
| **可观测** | 每个模型轮次记 `run_steps`（请求、响应、token、延迟、`prompt@hash`、错误） |

## Agent 拓扑

所有 agent 都是同一套 handler 签名 `async def handler(handle: RunHandle) -> dict`，通过 `@register(kind)` 注册：

| 层 | Agent | 做什么 |
|---|---|---|
| 业务 Run | `parse_job` | 原始 JD → 结构化岗位，只回填原文明确出现的字段 |
| | `extract_claims` | 导入简历 → 候选证据条目 |
| | `generate_resume` | 只拿已确认证据，重组 `resume.json`，不新增事实 |
| | `evaluate_resume` | **4 个 critic 并行**（岗位匹配/成果量化/技术深度/表达），各看各的维度 |
| | `scan_project` | **scout → 按模块并行 reader → synthesizer**，reader 之间上下文隔离 |
| | `project_guide` | 只基于项目证据生成导学与面试问答 |
| | `interview_plan / score / report` | planner 定契约、interviewer 出题不评分、evaluator 只看单题 |
| 对话 Run | `chat` | 独立队列；一轮用户消息 = 一个 Run；工具由自己调度、执行前拦截风险 |
| 嵌套子 agent | `investigate` | 同 Run 内的只读调查，独立上下文、只回结论、最多 6 轮 |
| | critics / readers | 共用 `RunHandle`：网络并发，库写由 `asyncio.Lock` 串行 |

关键点：并行只发生在**模型调用**上。SQLite 单写者，所有 agent 共用一个 `AsyncSession`，写操作走 `RunHandle` 内部的锁；单个 critic / reader 失败只污染自己那段结果，不拖垮整个 Run。

## 工具层：契约先于能力

模型只能输出 JSON 入参，其余由 `ToolRegistry` 强制：

| 风险 | 行为 |
|---|---|
| `read` | 直接执行；同一轮内相同工具+参数**记忆化**，避免模型原地打转 |
| `write` | 直接执行并回显结果 |
| `destructive` | 不执行，返回 `pending_confirmation`，生成确认卡 |
| `slow` | 立即返回 `run_id`，作为子 Run 挂到本轮（`parent_run_id`），不阻塞回答 |

- **路径白名单在工具层校验**，不看模型怎么说；项目扫描只读，`git` 仅允许 `log/rev-list/diff --stat`。
- **可见性按调用方过滤**：`audiences=("agent"|"chat"|"investigate", ...)`，调查子 agent 拿不到写工具。
- **`ToolError` 是数据不是崩溃**：预期失败作为工具结果回灌模型，Run 继续；只有未捕获异常才改 Run 状态。
- 共 29 个对话工具，覆盖岗位、证据、草稿、项目、评价、面试与 Run 日志检索。

## 上下文与续跑

- **业务 agent**：`assemble()` 按块装载，装不下先截断、再丢弃，`dropped` 写进快照。
- **对话助手**：`chat_entries` append-only；每次调用重建上下文，最近 2 轮工具结果保留全文，更早的换成确定性摘要；估算占用超窗口 60% 自动压缩，压缩失败降级硬截断；`/compact` 可手动触发。
- **确认后续跑**：点确认会创建一个续跑 Run，确定性执行已批准的调用，再把结果喂回模型继续本轮；未批准的动作模型看不到。
- **流式**：`token.delta` 只走 Redis 实时通道、不落库，避免事件表膨胀；最终文本以 `run_steps.response_json` / `chat.message` 为准，重连不补分片。
- **提示词版本化**：按内容 hash 存 `prompts` 表，同名内容变化自动停用旧版本；step 记录 `prompt@hash`，历史可解释。

## Harness 不变量

1. **单一执行入口**：所有 agent 调用经 `RunExecutor`，业务代码不直接调模型。
2. **工具契约**：入参 Pydantic 校验，路径白名单在工具层强制，模型不能拼 shell。
3. **上下文有预算**：`ContextBudget` 显式截断并记录丢弃项。
4. **状态在库里**：`runs` 状态机；worker 重启把遗留 `running` 标为 `interrupted`，不假装续跑。
5. **可回放**：`run_events(seq)` + `run_steps`（含 prompt 版本、token、延迟）+ SSE 重放。
6. **提示词版本化**：`prompts` 表按内容 hash 存版本，step 记录 `prompt@hash`。
7. **人在环**：证据条目需人工确认；评价只出报告；导出门禁校验 claim 引用。

## 目录

```
backend/          FastAPI + Celery + Alembic
  app/harness/    执行器、事件、工具注册表、上下文装配、提示词版本
  app/llm/        模型适配层
  app/models/     全部 ORM 模型
  app/routers/    API 路由
  scripts/        dev.py（启动器）、smoke_llm.py（模型自检）
frontend/         Vue 3 应用
data/             SQLite、简历、项目扫描产物、artifact
skill库/          提示词与模板资产（四个 skill + 18 套模板）
docs/adr/         架构决策记录
CONTEXT.md        领域术语表
```

## 启动

前置：Python ≥ 3.11、Node ≥ 20、Docker Desktop、本机 Edge（PDF 导出用）。

```bash
# 1. 配置
cp .env.example .env
# 填入 DASHSCOPE_API_KEY

# 2. Redis
docker compose up -d

# 3. 后端依赖与建库
cd backend
uv sync
uv run alembic upgrade head

# 4. 一键起 API + worker
uv run python scripts/dev.py

# 5. 前端（另开终端；或直接访问 8000，后端托管 dist）
cd frontend
npm install
npm run dev      # 开发模式，5173 代理到 8000
npm run build    # 生产构建，后端 / 直接托管
```

访问 http://127.0.0.1:8000/

## M0 验收

| 项 | 命令 / 操作 |
|---|---|
| 模型三项自检 | `cd backend && uv run python scripts/smoke_llm.py` |
| 迁移在空库可一次成功 | `DB_PATH=<新库绝对路径> uv run alembic upgrade head` |
| 发起 Run 并看流式事件 | 首页「发起自检 Run」，Run 抽屉实时滚动 |
| 关浏览器任务继续 | 关页面后 `GET /api/runs/{id}` 仍推进 |
| worker 崩溃标记中断 | kill worker → 重启 → run 变 `interrupted` |
| SSE 断线重放 | `GET /api/runs/{id}/stream?last_event_id=<seq>` |
| 回放一次 Run | `/runs/{id}` 页的「模型调用步骤」表 |
| 单元测试 | `uv run pytest -q` |
| 对话步骤可见 | `/assistant` 发一条需要调工具的消息，展开「执行过程」应看到模型轮次 → 工具调用 → 结果 |
| 确认续跑 | 让助手删一个岗位，出现确认卡；点确认后产生新的续跑 Run 并继续回答 |
| 关页面任务继续 | 发起一轮后关闭标签页，重新打开从事件表补齐 |

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/system/health` | DB / Redis / 模型状态 |
| GET | `/api/settings` | 只读配置，密钥掩码 |
| POST | `/api/runs` | 创建并投递任务 |
| GET | `/api/runs` | 任务列表 |
| GET | `/api/runs/{id}` | 任务详情 |
| GET | `/api/runs/{id}/events?after=` | 事件历史 |
| GET | `/api/runs/{id}/stream` | SSE（支持 `Last-Event-ID`） |
| GET | `/api/runs/{id}/steps` | 模型调用回放 |
| POST | `/api/runs/{id}/cancel` | 取消 |
| GET | `/api/artifacts/{id}` | 下载产物 |
| POST | `/api/jobs` | 粘贴原文 JD，落盘并自动解析 |
| GET | `/api/jobs?status=&q=` | 岗位列表，支持状态筛选与技能搜索 |
| GET | `/api/jobs/{id}` | 岗位详情（解析结果 + 原始 JD） |
| PATCH | `/api/jobs/{id}` | 编辑字段 / 手动设置投递状态 |
| POST | `/api/jobs/{id}/reparse` | 重新解析 |
| DELETE | `/api/jobs/{id}` | 删除岗位与原文目录 |
| POST | `/api/resumes/imported` | 上传 PDF（multipart），抽取文本 |
| GET | `/api/resumes/imported?q=` | 导入简历列表，`q` 在正文中搜索 |
| GET | `/api/resumes/imported/{id}` | 详情（含抽取文本） |
| PATCH | `/api/resumes/imported/{id}` | 改标题 / 标签 / 备注 |
| GET | `/api/resumes/imported/{id}/file` | 内嵌预览原 PDF |
| DELETE | `/api/resumes/imported/{id}` | 删除记录与文件 |
| GET | `/api/templates` | 19 套模板（ASu + 18 套变体） |
| GET/PUT | `/api/profile` | 候选人基本信息 |
| GET/POST | `/api/ledgers` | 证据账本（`?kind=personal\|project`） |
| PATCH/DELETE | `/api/ledgers/{id}` | 重命名 / 删除账本（被简历引用时拒绝） |
| GET/POST | `/api/ledgers/{id}/claims` | 证据条目 |
| POST | `/api/ledgers/{id}/import-project-evidence` | 项目扫描证据导入账本（默认待确认） |
| POST | `/api/ledgers/{id}/copy-claims` | 条目复制到其它账本（项目账本→个人账本） |
| PATCH/DELETE | `/api/claims/{id}` | 改状态/内容、删除 |
| POST | `/api/claims/bulk-status` | 批量确认/不采用 |
| POST | `/api/claims/extract-from-resume` | 从导入简历提炼证据（异步 run） |
| GET/POST | `/api/drafts` | 简历草稿 |
| GET/PATCH | `/api/drafts/{id}` | 草稿详情（含门禁结果）/ 保存 |
| POST | `/api/drafts/{id}/generate` | AI 生成初稿（异步 run） |
| GET | `/api/drafts/{id}/preview?template=` | 渲染预览 HTML |
| POST | `/api/drafts/{id}/export` | 门禁校验 + 导出 PDF，产生版本 |
| GET | `/api/versions` | 版本列表 |
| GET | `/api/versions/{id}` | 版本详情（含 resume.json） |
| GET | `/api/versions/{id}/pdf` | 下载/预览 PDF（`?download=true`） |
| GET | `/api/versions/{id}/html` | 版本 HTML |
| POST | `/api/versions/{id}/re-render?template=` | 换模板重渲染 |
| DELETE | `/api/versions/{id}` | 删除版本与文件 |
| POST | `/api/evaluations` | 发起评价（异步 run，四个 critic 并行） |
| GET | `/api/evaluations?draft_id=&job_id=` | 评价列表 |
| GET | `/api/evaluations/{id}` | 报告详情（含 findings） |
| DELETE | `/api/evaluations/{id}` | 删除评价 |
| GET/POST | `/api/projects` | 项目列表 / 添加本地目录（白名单校验） |
| GET/PATCH/DELETE | `/api/projects/{id}` | 详情（含证据与导学）/ 编辑 / 删除 |
| POST | `/api/projects/{id}/scan` | 发起扫描（异步 run） |
| POST | `/api/projects/{id}/guide` | 生成导学（异步 run） |
| GET | `/api/projects/{id}/guides/{guide_id}` | 导学正文 |
| GET | `/api/projects/{id}/evidence/{eid}/source` | 核验证据引用，返回代码上下文 |
| GET | `/api/projects/{id}/file?path=` | 读取项目内文件（只读、白名单） |
| POST | `/api/interviews` | 新建面试会话（异步生成契约） |
| GET | `/api/interviews` | 会话列表 |
| GET | `/api/interviews/{id}` | 会话详情（含轮次与进度） |
| POST | `/api/interviews/{id}/next` | 同步出下一题（含追问） |
| POST | `/api/interviews/{id}/answer` | 提交回答，异步评分 |
| POST | `/api/interviews/{id}/skip` | 跳过当前题 |
| POST | `/api/interviews/{id}/report` | 生成终局报告（异步） |
| POST | `/api/interviews/{id}/abandon` | 放弃会话 |
| DELETE | `/api/interviews/{id}` | 删除会话 |
| GET/POST | `/api/chat/sessions` | 对话会话列表 / 新建 |
| PATCH/DELETE | `/api/chat/sessions/{id}` | 重命名 / 删除会话 |
| GET | `/api/chat/sessions/{id}` | 会话详情（轮次、计划、卡片、待确认动作） |
| POST | `/api/chat/sessions/{id}/turns` | 发起一轮对话，返回 turn_id + run_id |
| POST | `/api/chat/sessions/{id}/compact` | 手动压缩上下文，返回四段摘要 |
| POST | `/api/chat/turns/{id}/cancel` | 取消本轮（不级联子 Run） |
| POST | `/api/chat/actions/{id}/confirm` | 确认动作，创建续跑 Run |
| POST | `/api/chat/actions/{id}/reject` | 取消动作 |
| GET | `/api/chat/tools` | 助手可用工具及风险等级 |
| GET | `/api/runs/events/search` | 跨 Run 检索事件摘要（默认排除流式分片） |

## 对话助手

左侧导航第一项「助手」，独立页面 `/assistant`。一轮用户消息 = 一个 `kind=chat` 的 Run，走独立 Celery 队列，离开页面任务继续跑，回来从事件表补齐。

- **每步可见**：计划块、模型轮次、工具调用与结果、只读调查、上下文压缩都作为事件记录并渲染；慢任务卡片可展开订阅该 Run 的 SSE。
- **工具统一**：全部注册进 `harness/registry.py`，按 `risk` 分级：只读直接执行；轻写直接执行并回显；高影响（创建/删除/导出/取消）生成确认卡；慢任务立即返回 `run_id` 并挂到本轮（`runs.parent_run_id`）。
- **确认后续跑**：点确认会创建续跑 Run，确定性执行已批准的工具调用，再把结果喂回模型继续本轮。未批准的动作模型看不到。
- **上下文**：`chat_entries` append-only；每次调用重建上下文，最近 2 轮工具结果保留全文，更早的用确定性摘要替换；占用超过窗口 60% 自动压缩，失败降级为硬截断；`/compact` 可手动触发。
- **主动调查**：`investigate` 派同 Run 内的只读子 agent，最多 6 轮、每轮 1 次、深度 1，只回结论。
- **日志检索**：`list_runs` / `get_run` / `search_run_events` / `watch_run`，默认排除 `token.delta`；单次返回上限 50 条。
- **同名重复调用**：同一轮内相同工具+参数的只读调用直接复用结果，避免模型原地打转。
- **流式输出**：`token.delta` 只走 Redis 实时通道，不落库；最终文本落 `chat.message` 与条目，重连以最终文本为准。

## 模拟面试行为

- **角色分离**：planner 生成 10 题契约（project 4 / job_fit 3 / behavior 2 / closing 1，每题绑定 claim id 与项目证据 id）；interviewer 逐轮出题，**不评分**；evaluator 只看单题问答打分，**不提问**；reporter 汇总终局报告。
- **推进规则**：主问题优先。只有回答空泛、回避或与简历冲突时才追问，且**每题最多追问 1 次**；超过后强制推进下一道主问题与下一阶段。提示词里显式带上「下一道待问主问题」，防止模型原地打转。
- **可暂停**：每轮问答落库，关掉页面改天继续；不做自动超时。
- **跳过**：允许跳过，标记 `skipped`，不计入得分。会话内容为空时不能生成报告。
- **评分口径**：事实一致性权重最高——回答与简历证据冲突时该项不得高于 2 分；编造技术细节直接 1 分。
- **报告**：总分、五维（事实一致性/技术深度/岗位匹配/表达结构/追问韧性）、verdict、优点、短板、red flags（需要你自己核实）、下一步准备、逐题复盘。
- **上下文快照**：创建会话时冻结简历与岗位 JD，后续修改不影响历史会话的口径。

## 项目扫描与证据行为

- **安全边界**：路径必须在 `PROJECT_ROOTS` 内，校验在工具层，不交给模型。只读，不执行任意 shell；`git` 仅允许 `log/rev-list/diff --stat` 等只读查询。
- **硬性忽略**：`.git`、`.venv`、`node_modules`、`dist`、`build`、`__pycache__`、`chroma_db`、`data`、`assets` 等；单文件 > 200KB 跳过。
- **三级 agent**：scout 判断项目性质并给模块排优先级 → 按模块并行 reader（每模块 8k 字符预算、最多 6 个文件，均分后补给大文件）→ synthesizer 合并。单个 reader 失败不影响其余模块。
- **证据必须可核验**：每条证据至少一个 `{path, line}`，`line>0` 且路径必须是文件。综合阶段编造的目录引用、`line:0` 会被丢弃。
- **可回查**：前端「查看代码」调用 `/evidence/{id}/source`，返回该行前后 4 行，并标出文件是否仍存在。实测 66 条引用全部可解析，行号与内容吻合。
- **导学**：只使用项目证据生成 Markdown，包含架构调用链、可用的简历描述、核心模块拆解、12–18 组面试问答与薄弱点。同类导学只保留最新一份。

## 简历评价行为

- **四个独立 critic 并行**：岗位匹配、成果量化、技术深度、表达与结构。各自独立上下文，避免互相锚定；共用 `RunHandle` 但库写由 `asyncio.Lock` 串行化，网络调用仍然并发。
- **单个 critic 失败不拖垮整份报告**：失败的维度记为 0 分并在 `summary.failed_critics` 中列出，其余维度正常输出。
- **`where` 强约束**：只接受 `section[i]`、`section[i].bullets[j]`、`profile.field`、`__missing__` 四种形式；模型输出的 `.text` 后缀会被剥掉，无法识别的位置置空（前端不显示定位按钮）。
- **定位闭环**：报告里每条 findings 可跳回编辑器 `?focus=<where>`，编辑器滚动到该条目并黄框高亮。
- **快照**：评价时把 `resume.json` 与岗位 JD 存入 `evaluations`，草稿后续修改不影响历史报告的可解释性。
- **只出报告**：不写回草稿，不做 JSON Patch。你决定改什么、怎么改。

## 生成简历行为

- **证据门禁**：每个 bullet / skills / summary 条目必须带非空 `claim_ids`；引用的条目必须属于同一账本且状态为 `confirmed`。任一不满足，导出返回 409 并列出所有违规位置，前端在编辑器顶部展示。
- **AI 只重组不新增**：撰写 agent 只拿到已确认证据，输出后由 `_normalize` 再次过滤非法 claim id；模型无法绕过门禁写入不存在的事实。
- **模板与内容解耦**：`resume.json` 是唯一事实源。模板只是一组 body 变体类 + 布局（flow / sidebar / two-column）。换模板只重渲染，不产生新版本。
- **19 套模板**：ASu 单栏（默认）+ 18 套变体。双栏模板按内容长度自然分页，不强制制造空白第二页。
- **版本不可变**：每次导出生成一个 `ResumeVersion`，`version_no` 按账本+岗位递增，产物写入各自的 `resumes/generated/version-{id}/` 目录（`resume.json` + `index.html` + `resume.pdf`），后续导出或换模板重渲染不会覆盖历史版本。删除版本时清理对应目录；若旧数据中多个版本共用同一目录，最后一个引用删除后才清理。
- **编辑器**：左侧结构化表单，右侧 iframe 实时预览；修改后 700ms 防抖自动保存，保存即重渲染。
- **教育字段兜底**：学校/专业/时间直接来自 education 类证据，即使模型只输出被过滤的重复内容，也不会丢失整段教育经历。

## 简历库行为

- **两类账本**：`个人账本` 汇总可写进生成简历的事实；`项目账本` 一项目一本，绑定 `projects.id`，用于把扫描证据逐条确认后再转入个人账本。生成简历只列举个人账本，保证门禁口径单一。
- **项目账本流转**：扫描产出 `ProjectEvidence` →「从项目证据导入」生成待确认 `Claim`（带 `path:line` 来源）→ 人工确认 →「转入个人账本」保留状态复制一份。删除项目时，若账本已被简历引用则脱钩为「已归档：xxx」，否则连带删除。
- 两类简历严格分开：`导入简历` 只保留原 PDF + `extracted.txt`，不转结构化；`生成简历` 在 M3 提供。
- 抽取使用 `pypdf` 默认模式。实测中文双栏 PDF 的阅读顺序正确；`extraction_mode="layout"` 可保留分栏位置，但 M2 不落库。
- 无文本层（扫描件）直接拒绝，返回 422，不落文件、不做 OCR。
- 加密 PDF 拒绝，不尝试破解。
- 单文件上限 20MB。

## 岗位解析行为

- 原文 JD 同时写入数据库与 `data/jobs/{id}/raw-{raw_id}.txt`，永不修改。
- 解析失败会把 `ValueError: ...` 写回 `jobs.parse_error`，前端直接展示；`POST /reparse` 可重试。
- 解析结果只回填原文明确出现的字段。公司名缺失时保持空字符串，不猜测。
- `ambiguities` 记录原文中不自洽或缺失的信息，供你自己判断。
- 投递状态（收藏 / 已投 / 面试中 / 挂）只由手动设置，系统从不推断。

## 里程碑

- **M0**（已完成）：Run 底座、事件流、SSE 重放、模型适配、前端壳
- **M1**（已完成）：岗位库：粘贴原文 JD → 结构化解析 → 卡片 → 手动投递状态
- **M2**（已完成）：简历库（导入）：上传 PDF → 抽取文本 → 预览/全文搜索
- **M3**（已完成）：生成简历：证据账本 → AI 生成 → 结构化编辑 → 19 套模板 → 门禁校验 → PDF 导出
- **M4**（已完成）：简历评价：四个 critic 并行 → 结构化 findings → 定位跳转
- **M5**（已完成）：项目导学：源码扫描 → scout/reader/synthesizer → 带 `file:line` 的项目证据 → 面经
- **M6**（已完成）：模拟面试：planner/interviewer/evaluator 三角色分离，逐答评分 + 终局报告

## 注意

- Celery 官方不支持 Windows，worker 必须用 `-P threads`（`scripts/dev.py` 已处理）。
- **必须同时起两个 worker**：主队列 `-Q celery -c 4`（长任务）与对话队列 `-Q chat -c 2`。只起一个会让对话被扫描任务堵住。`scripts/dev.py` 已经分开起两个进程。
- 不要用 `uvicorn --reload`：重载子进程会继承套接字，导致端口残留。
- 对话上下文窗口默认按 32768 估算（`.env` 的 `CHAT_CONTEXT_WINDOW`），首次长会话后按 `chat.compaction` 事件的 `tokens_before` 校准。
- `.env` 不入库；API Key 只在后端读取。
