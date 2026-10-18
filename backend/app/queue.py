"""任务入队：API 侧唯一入口。

队列拆分：
- 主队列（默认）：业务 agent（扫描、评价、生成、导学、面试）
- chat 队列：对话轮次与确认续跑，独占并发，避免被长任务堵住
"""
from __future__ import annotations

from app.worker import celery_app

DEFAULT_QUEUE = "celery"
CHAT_QUEUE = "chat"


def enqueue_run(run_id: int, queue: str = DEFAULT_QUEUE) -> str:
    """投递到 Celery；返回 task id。Redis 不可用时抛出，由路由转 503。"""
    result = celery_app.send_task("app.worker.run_task", args=[run_id], queue=queue)
    return result.id
