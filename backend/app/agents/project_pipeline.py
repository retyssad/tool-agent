"""项目扫描与导学的 run handler + 证据归一化。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from app.agents.project_analyzer import GUIDE_PROMPT, _load, run_pipeline
from app.config import get_settings
from app.harness import events as ev
from app.harness.executor import RunHandle
from app.models.project import Project, ProjectEvidence, ProjectGuide
from app.tasks import register
from app.tools.scan import ScanError, scan_project

settings = get_settings()

CATEGORIES = {"architecture", "feature", "quality", "data", "ops"}


@register("scan_project")
async def scan_project_run(handle: RunHandle) -> dict[str, Any]:
    from app.db import SessionLocal

    project_id = int(handle.target.get("project_id") or 0)
    if not project_id:
        raise ValueError("缺少 project_id")

    async with SessionLocal() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise ValueError(f"项目不存在: {project_id}")

        async def mark_failed(reason: str) -> None:
            async with SessionLocal() as fresh:
                row = await fresh.get(Project, project_id)
                if row is not None and row.scan_status == "running":
                    row.scan_status = "failed"
                    row.scan_error = reason[:500]
                    await fresh.commit()

        handle.on_failure = mark_failed

        project.scan_status = "running"
        project.scan_error = ""
        await session.commit()

        try:
            await handle.log(f"开始扫描 {project.path}")
            scan = await asyncio.to_thread(scan_project, project.path)
        except ScanError as exc:
            project.scan_status = "failed"
            project.scan_error = str(exc)
            await session.commit()
            raise

        await handle.emit(
            ev.RUN_LOG,
            {
                "message": f"扫描完成：{scan.file_count} 个文件，{scan.module_count} 个模块，{scan.total_lines} 行",
                "level": "info",
            },
        )

        if scan.file_count == 0:
            project.scan_status = "failed"
            project.scan_error = "没有找到可分析的源码文件（检查目录与忽略规则）"
            await session.commit()
            raise ValueError(project.scan_error)

        merged = await run_pipeline(handle, scan)

        # 落盘扫描结果
        folder = settings.data_dir / "projects" / str(project.id)
        folder.mkdir(parents=True, exist_ok=True)
        scan_path = folder / "scan.json"
        await asyncio.to_thread(
            scan_path.write_text, json.dumps(scan.as_dict(), ensure_ascii=False, indent=2), "utf-8"
        )
        analysis_path = folder / "analysis.json"
        await asyncio.to_thread(
            analysis_path.write_text, json.dumps(merged, ensure_ascii=False, indent=2), "utf-8"
        )

        # 重建证据（同一次扫描视为全量刷新）
        await session.execute(delete(ProjectEvidence).where(ProjectEvidence.project_id == project.id))
        evidence_items = _normalize_evidence(merged.get("evidence"))
        for item in evidence_items:
            session.add(
                ProjectEvidence(
                    project_id=project.id,
                    statement=item["statement"],
                    refs=item["refs"],
                    module=item["module"],
                    category=item["category"],
                    capabilities=item["capabilities"],
                    run_id=handle.run_id,
                )
            )

        info = merged.get("project") or {}
        project.name = str(info.get("name") or project.name)[:200]
        project.description = str(info.get("overview") or project.description)
        project.tech_stack = [str(t)[:40] for t in (info.get("tech_stack") or [])][:20]
        project.last_scanned_at = datetime.now(timezone.utc)
        project.scan_status = "succeeded"
        project.scan_error = ""
        project.scan_run_id = handle.run_id
        project.scan_path = f"projects/{project.id}/scan.json"
        project.scan_summary = {
            "file_count": scan.file_count,
            "module_count": scan.module_count,
            "total_lines": scan.total_lines,
            "evidence_count": len(evidence_items),
            "interview_questions": len(merged.get("interview_questions") or []),
            "weak_spots": merged.get("weak_spots") or [],
            "one_liner": info.get("one_liner") or "",
            "git": scan.git,
        }
        await session.commit()

        await handle.emit(
            ev.ARTIFACT_CREATED,
            {
                "kind": "project_scan",
                "project_id": project.id,
                "evidence_count": len(evidence_items),
            },
        )
        return {
            "project_id": project.id,
            "evidence_count": len(evidence_items),
            "modules": scan.module_count,
            "files": scan.file_count,
        }


@register("project_guide")
async def project_guide_run(handle: RunHandle) -> dict[str, Any]:
    from app.db import SessionLocal

    project_id = int(handle.target.get("project_id") or 0)
    kind = str(handle.target.get("kind") or "interview")
    if not project_id:
        raise ValueError("缺少 project_id")

    async with SessionLocal() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise ValueError("项目不存在")
        evidence = (
            await session.scalars(
                select(ProjectEvidence).where(ProjectEvidence.project_id == project_id)
            )
        ).all()
        if not evidence:
            raise ValueError("该项目还没有证据，请先扫描")

        analysis_path = settings.data_dir / "projects" / str(project_id) / "analysis.json"
        analysis: dict[str, Any] = {}
        if analysis_path.is_file():
            analysis = json.loads(await asyncio.to_thread(analysis_path.read_text, encoding="utf-8"))

        facts = {
            "project": {
                "name": project.name,
                "one_liner": (project.scan_summary or {}).get("one_liner", ""),
                "overview": project.description,
                "tech_stack": project.tech_stack,
            },
            "evidence": [
                {
                    "statement": e.statement,
                    "category": e.category,
                    "capabilities": e.capabilities,
                    "module": e.module,
                    "refs": e.refs,
                }
                for e in evidence
            ],
            "interview_questions": analysis.get("interview_questions", []),
            "weak_spots": analysis.get("weak_spots", []),
        }

        prompt_hash = await handle.use_prompt("project.guide", GUIDE_PROMPT, "项目导学")
        reply = await handle.call_model(
            [
                {"role": "system", "content": GUIDE_PROMPT},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
            ],
            agent="project.guide",
            prompt_name="project.guide",
            prompt_hash=prompt_hash,
            temperature=0.3,
        )
        content = reply.content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.lstrip().startswith("markdown"):
                content = content.lstrip()[9:]
            content = content.strip()
        if len(content) < 200:
            raise ValueError(f"导学内容过短（{len(content)} 字），生成失败")

        title = f"{project.name} · {'面经' if kind == 'interview' else '源码课程'}"
        # 同类导学只保留最新一份
        await session.execute(
            delete(ProjectGuide).where(
                ProjectGuide.project_id == project_id, ProjectGuide.kind == kind
            )
        )
        guide = ProjectGuide(
            project_id=project_id,
            kind=kind,
            title=title,
            content_md=content,
            run_id=handle.run_id,
        )
        session.add(guide)
        await session.commit()
        await session.refresh(guide)

        folder = settings.data_dir / "projects" / str(project_id)
        await asyncio.to_thread((folder / f"guide-{kind}.md").write_text, content, "utf-8")

        await handle.emit(
            ev.ARTIFACT_CREATED,
            {"kind": "project_guide", "project_id": project_id, "guide_id": guide.id},
        )
        return {"guide_id": guide.id, "chars": len(content)}


def _normalize_evidence(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        statement = str(raw.get("statement") or "").strip()
        if not statement or statement in seen:
            continue
        refs = _normalize_refs(raw.get("refs"))
        if not refs:
            # 没有代码引用的证据不可核验，直接丢弃
            continue
        seen.add(statement)
        category = str(raw.get("category") or "feature").strip()
        if category not in CATEGORIES:
            category = "feature"
        modules = raw.get("source_modules") or []
        out.append(
            {
                "statement": statement[:800],
                "refs": refs,
                "module": str(modules[0] if modules else "")[:200],
                "category": category,
                "capabilities": [str(c)[:40] for c in (raw.get("capabilities") or [])][:10],
            }
        )
    return out[:24]


def _normalize_refs(refs: Any) -> list[dict[str, Any]]:
    """只接受带真实行号的文件路径。目录路径、line=0、占位符一律丢弃。"""
    out: list[dict[str, Any]] = []
    if not isinstance(refs, list):
        return out
    for raw in refs:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip().replace("\\", "/")
        if not path or path.startswith("/") or ".." in path:
            continue
        if "." not in Path(path).name:
            # 目录或占位符（如 "scout"），不是可核验的文件引用
            continue
        try:
            line = int(raw.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        if line <= 0:
            continue
        out.append(
            {
                "path": path[:300],
                "line": line,
                "snippet": str(raw.get("snippet") or "")[:160],
            }
        )
    return out[:4]
