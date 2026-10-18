"""Celery worker。

注意：Windows 上 Celery 官方不支持 prefork，必须用 threads pool。
启动：celery -A app.worker.celery_app worker -P threads -c 4 -l info
"""
from __future__ import annotations

import asyncio
import socket
import sys

from celery import Celery
from celery.signals import worker_ready
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.harness.executor import execute_run, mark_orphaned_runs
from app.models.harness import Run
from app.tasks import get_handler

settings = get_settings()
celery_app = Celery("job_workbench", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    timezone="Asia/Shanghai",
)


@worker_ready.connect
def _on_worker_ready(**_kwargs) -> None:
    """把上次进程遗留的 running run 标为 interrupted。"""
    try:
        count = asyncio.run(mark_orphaned_runs())
        if count:
            print(f"[worker] 标记 {count} 个遗留 run 为 interrupted", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] 孤儿 run 清理失败: {exc}", file=sys.stderr)


@celery_app.task(name="app.worker.run_task")
def run_task(run_id: int) -> None:
    asyncio.run(_dispatch(run_id))


async def _dispatch(run_id: int) -> None:
    async with SessionLocal() as session:
        row = await session.execute(select(Run.kind).where(Run.id == run_id))
        kind = row.scalar_one_or_none()
    if not kind:
        return
    handler = get_handler(kind)
    if handler is None:
        raise RuntimeError(f"未注册的任务类型: {kind}")
    await execute_run(run_id, handler, worker_id=socket.gethostname())
