# Celery + Redis，但 API 与 worker 都跑在 Windows 原生进程

本机自用工作台，需要后台 agent 任务在关掉浏览器后继续执行。选择 Celery + Redis（Redis 跑在 Docker Desktop），而 API 与 worker 都以原生 Windows 进程运行，worker 使用 `--pool=threads`。理由：所有业务 IO 都在 Windows 文件系统上——读取 `D:\` 下的项目源码、调用本机 Edge 导出 PDF、访问 SQLite。把 worker 放进 WSL2 会让 SQLite 走 DrvFs（WAL 锁不可靠）、项目路径变成 `/mnt/d`（IO 显著变慢），因此拒绝"全容器化"。

代价必须被接受：Celery 官方不支持 Windows，`prefork` 不可用；`soft_time_limit`、`--max-tasks-per-child` 等 prefork 专属能力缺失；worker 重启会丢失正在执行的任务（状态留在 `runs` 表，标记为 `interrupted`，允许重跑）。

## 事件通道

任务事件双写：`run_events` 表（持久化、可回放，SSE 断线重连的真相源）+ Redis pub/sub（实时推送）。API 订阅 pub/sub 转发给前端 SSE。worker 是独立进程，SSE 绝不能依赖 worker 内存中的状态。

## Consequences

只有一个部署形态：`docker compose up -d redis` + 两个本地进程（`uvicorn`、`celery`）。换机器时要重装 Python/Node 与 Edge；换操作系统时 Celery 的 Windows 限制消失，但事件双通道仍需保留。
