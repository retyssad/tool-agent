# 对话轮次作为 Run 执行，走独立队列

工作台助手原来是一次同步 HTTP 调用：`POST /api/chat/sessions/{id}/messages` 直接 `await run_chat(...)`，用 `RunHandle(run_id=0)` 借模型能力（`routers/chat.py`）。`RunHandle.emit` 在 `run_id<=0` 时直接返回，所以对话的任何步骤都不进入 `run_events`，Run 面板看不见；请求也不能取消、不能重连、刷新即断。

## Decision

**一次用户消息 = 一个 `kind=chat` 的 Run。**

- 前端 `POST /api/chat/sessions/{id}/turns` 创建 Run 并立即返回 `turn_id + run_id`；回复通过该 Run 的 SSE 推送（复用 `/api/runs/{id}/stream`）。
- 对话 Run 投递到独立 Celery 队列 `chat`（`-Q chat`，并发 2）；业务任务继续走主队列（并发 4）。否则一次项目扫描占满线程池后，对话会排队几十秒。
- 一轮对话内部调用工具启动的慢任务（扫描、评价、导学、生成）是**另一个 Run**，通过 `runs.parent_run_id` 挂到本轮上；本轮不等待它结束，立即返回"已启动"。
- 高影响操作先登记 `chat_actions`（pending），用户确认后创建**续跑 Run**：确定性执行已批准的工具调用，把结果喂回模型继续本轮剩余步骤。不阻塞 worker，不伪造"模型已获授权"的上下文。
- 取消对话 Run **不级联**取消子 Run；UI 明确说明"取消只影响本轮"。
- 同一会话同时只允许一个执行中的轮次；冲突返回 409，避免 transcript 交错。

## Consequences

- 步骤流、token 统计、重放、断线补齐全部复用既有 Run 基建，不再另建一套可观测体系。
- `kind=chat` 的 Run 需要 `parent_run_id`、`chat_turns.current_run_id` 两个引用，迁移里一起加。
- 启动方式变化：`scripts/dev.py` 起两个 worker 进程；手动起 worker 时必须带 `-Q chat` 的那个，否则对话一直排队。
- 对话 layer 与业务 agent 共用 `RunExecutor` 与工具注册表（`ToolSpec.audiences` 区分调用方）。

## Rejected

- **API 进程内异步 + 自建 SSE**：需要另建取消、重连、重放、步骤记录，与"每一步都能看见"的目标冲突。
- **一个会话一个长 Run**：无法回放单轮、无法单独取消，事件流也会无界增长。
- **确认时阻塞 worker 等用户**：占线程、重启即丢、无法解释"为什么这一步一直不动"。
- **确认后只执行工具、不回到模型**：模型不知道执行结果，下一轮容易给出与事实脱节的建议。
