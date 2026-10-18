"""本地开发启动器：一条命令起 API + worker，日志分文件。

用法：
    uv run python scripts/dev.py          # 前台运行两者
    uv run python scripts/dev.py --api    # 只起 API
    uv run python scripts/dev.py --worker # 只起 worker
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"


def pump(proc: subprocess.Popen, name: str) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", action="store_true")
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    run_api = args.api or not (args.api or args.worker)
    run_worker = args.worker or not (args.api or args.worker)

    LOG_DIR.mkdir(exist_ok=True)
    procs: list[tuple[str, subprocess.Popen]] = []

    if run_api:
        procs.append(
            (
                "api",
                subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                ),
            )
        )
    if run_worker:
        # 主队列：长任务（扫描/评价）会占满线程，所以单列
        procs.append(
            (
                "worker",
                subprocess.Popen(
                    [sys.executable, "-m", "celery", "-A", "app.worker.celery_app", "worker",
                     "-P", "threads", "-c", "4", "-Q", "celery", "-l", "info"],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                ),
            )
        )
        # 对话队列：独立进程，避免被扫描任务堵住
        procs.append(
            (
                "chat",
                subprocess.Popen(
                    [sys.executable, "-m", "celery", "-A", "app.worker.celery_app", "worker",
                     "-P", "threads", "-c", "2", "-Q", "chat", "-l", "info",
                     "--hostname", "chat@%h"],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                ),
            )
        )

    threads = [threading.Thread(target=pump, args=(p, n), daemon=True) for n, p in procs]
    for t in threads:
        t.start()

    try:
        for name, proc in procs:
            code = proc.wait()
            print(f"[dev] {name} 退出，code={code}")
    except KeyboardInterrupt:
        for _, proc in procs:
            proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
