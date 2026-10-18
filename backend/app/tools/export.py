"""HTML → PDF，调用本机 Edge/Chrome 的 CDP。

不引入 puppeteer，复用 skill 库的零依赖脚本。产物写到目标路径。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_EXPORT = REPO_ROOT / "skill库" / "scripts" / "export-resume-pdf.mjs"


class ExportError(RuntimeError):
    pass


def export_pdf(html_path: Path, pdf_path: Path, *, timeout_s: int = 90) -> Path:
    if shutil.which("node") is None:
        raise ExportError("未找到 node，无法导出 PDF")
    if not SKILL_EXPORT.is_file():
        raise ExportError(f"导出脚本缺失: {SKILL_EXPORT}")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "node",
            str(SKILL_EXPORT),
            str(html_path),
            "--out",
            str(pdf_path),
        ],
        cwd=str(REPO_ROOT / "skill库"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    if proc.returncode != 0 or not pdf_path.is_file():
        detail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise ExportError(f"PDF 导出失败: {detail}")
    return pdf_path
