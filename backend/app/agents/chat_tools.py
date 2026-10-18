"""对话工具集：注册进 harness 的 ToolRegistry，按 audience 区分调用方。

所有工具仍通过本机 HTTP 调现有 REST API：对话与页面走同一条写入路径，
不会出现"助手做了但页面看不见"的行为漂移。

风险分级（ToolSpec.risk）：
- read         只读，直接执行
- write        轻写，直接执行并回显
- destructive  生成确认卡，确认后由续跑 Run 执行
- slow         立即返回 run_id，作为子 Run 挂到本轮
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.harness.registry import ToolError, registry

settings = get_settings()
TIMEOUT = 30.0


class ToolFailure(Exception):
    pass


# ---------- 运行时上下文 ----------

@dataclass(slots=True)
class ChatRuntime:
    """一次对话轮次的运行时。工具通过 contextvar 拿到它。"""

    session: Any                      # AsyncSession：chat_* 表
    handle: Any                       # RunHandle：事件与模型
    session_id: int
    turn_id: int
    run_id: int
    tool_call_id: str = ""
    confirmed: bool = False
    pending: list[dict[str, Any]] = field(default_factory=list)
    cards: list[dict[str, str]] = field(default_factory=list)
    child_runs: list[int] = field(default_factory=list)
    # 同一轮最多 1 次主动调查（见 ADR-0005），避免延迟与成本失控
    investigations: int = 0
    # 同一轮内相同工具+参数的调用直接复用，防止模型原地打转
    cache: dict[str, Any] = field(default_factory=dict)
    investigate: Callable[[str], Awaitable[dict[str, Any]]] | None = None

    @property
    def api_base(self) -> str:
        return settings.api_base


_runtime: ContextVar[ChatRuntime | None] = ContextVar("chat_runtime", default=None)


def current_runtime() -> ChatRuntime:
    rt = _runtime.get()
    if rt is None:
        raise ToolFailure("对话运行时未初始化")
    return rt


def bind_runtime(rt: ChatRuntime):
    return _runtime.set(rt)


def unbind_runtime(token) -> None:
    _runtime.reset(token)


async def _request(method: str, path: str, **kwargs) -> Any:
    rt = current_runtime()
    async with httpx.AsyncClient(base_url=rt.api_base, timeout=TIMEOUT) as client:
        try:
            resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ToolFailure(f"调用接口失败：{exc}") from exc
    if resp.status_code >= 400:
        detail: Any = resp.text[:300]
        try:
            body = resp.json()
            detail = body.get("detail", detail)
        except Exception:  # noqa: BLE001
            pass
        if isinstance(detail, dict):
            detail = detail.get("message") or str(detail)[:300]
        raise ToolFailure(str(detail)[:400])
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {}


async def _tag_child(run_id: int | None) -> None:
    """把工具启动的慢任务挂到本轮 Run 上，便于"从对话追溯子 Run"。"""
    if not run_id:
        return
    rt = current_runtime()
    from sqlalchemy import update

    from app.models.harness import Run

    await rt.session.execute(
        update(Run).where(Run.id == int(run_id)).values(parent_run_id=rt.run_id)
    )
    await rt.session.commit()
    rt.child_runs.append(int(run_id))


# ---------- 参数模型 ----------

class Empty(BaseModel):
    pass


class ListJobsArgs(BaseModel):
    status: str | None = Field(default=None, description="saved/applied/interviewing/rejected")


class JobIdArgs(BaseModel):
    job_id: int


class SetJobStatusArgs(BaseModel):
    job_id: int
    status: str = Field(description="saved/applied/interviewing/rejected")


class CreateJobArgs(BaseModel):
    raw_text: str = Field(description="用户从 Boss 复制的完整 JD 原文")
    title_hint: str = ""


class DeleteJobArgs(BaseModel):
    job_id: int


class ListClaimsArgs(BaseModel):
    status: str | None = Field(default=None, description="pending/confirmed/expired/rejected")
    ledger_id: int | None = Field(
        default=None,
        description="限定某个账本；不传则汇总所有账本（个人账本优先）",
    )


class AddClaimArgs(BaseModel):
    statement: str = Field(description="一句可独立验证的事实陈述")
    kind: str = Field(default="other", description="experience/project/education/skill/campus/award/other")
    organization: str = ""
    role: str = ""
    period: str = ""
    ledger_id: int | None = None


class SetClaimStatusArgs(BaseModel):
    claim_ids: list[int]
    status: str = Field(description="confirmed/rejected/pending")


class ListDraftsArgs(BaseModel):
    pass


class CreateDraftArgs(BaseModel):
    ledger_id: int | None = None
    target_job_id: int | None = None
    title: str = "新建简历"
    template: str = "asu"


class DraftIdArgs(BaseModel):
    draft_id: int


class SetTemplateArgs(BaseModel):
    draft_id: int
    template: str


class ExportArgs(BaseModel):
    draft_id: int
    template: str | None = None


class EvaluateArgs(BaseModel):
    draft_id: int


class ListProjectsArgs(BaseModel):
    pass


class ProjectIdArgs(BaseModel):
    project_id: int


class GuideArgs(BaseModel):
    project_id: int
    kind: str = "interview"


class ListInterviewsArgs(BaseModel):
    pass


class InterviewArgs(BaseModel):
    job_id: int
    draft_id: int | None = None
    imported_resume_id: int | None = None


class AskUserArgs(BaseModel):
    question: str = Field(description="向用户提的问题")
    options: list[str] = Field(default_factory=list, description="可选项，前端渲染为按钮")


class PlanStep(BaseModel):
    label: str = Field(description="一步任务，动词开头，不超过 20 字")
    status: str = Field(default="pending", description="pending/running/done/failed")


class SetPlanArgs(BaseModel):
    steps: list[PlanStep] = Field(description="完整计划，覆盖之前的计划")


class ListRunsArgs(BaseModel):
    status: str | None = Field(default=None, description="queued/running/succeeded/failed/cancelled/interrupted")
    kind: str | None = Field(default=None, description="如 scan_project/evaluate_resume/chat")
    limit: int = 20


class RunIdArgs(BaseModel):
    run_id: int


class SearchEventsArgs(BaseModel):
    query: str = Field(default="", description="在事件摘要里匹配的关键词")
    run_ids: list[int] = Field(default_factory=list, description="限定这些 run；空表示全部")
    types: list[str] = Field(default_factory=list, description="限定事件类型；空表示全部（默认排除 token 流）")
    status: str | None = None
    kind: str | None = None
    since: str | None = Field(default=None, description="ISO 时间，含")
    until: str | None = Field(default=None, description="ISO 时间，不含")
    limit: int = 30


class WatchRunArgs(BaseModel):
    run_id: int
    last_seq: int = Field(default=0, description="只看 seq 之后的事件")


class CancelRunArgs(BaseModel):
    run_id: int


class InvestigateArgs(BaseModel):
    question: str = Field(description="要调查的问题，写清目标和范围")


# ---------- 工具实现 ----------

async def _get_overview(_: Empty) -> dict[str, Any]:
    return await _request("GET", "/api/system/overview")


async def _list_jobs(args: ListJobsArgs) -> dict[str, Any]:
    params = {"status": args.status} if args.status else {}
    rows = await _request("GET", "/api/jobs", params=params)
    return {"jobs": rows[:20], "total": len(rows)}


async def _get_job(args: JobIdArgs) -> dict[str, Any]:
    return await _request("GET", f"/api/jobs/{args.job_id}")


async def _set_job_status(args: SetJobStatusArgs) -> dict[str, Any]:
    return await _request("PATCH", f"/api/jobs/{args.job_id}", json={"status": args.status})


async def _create_job(args: CreateJobArgs) -> dict[str, Any]:
    rt = current_runtime()
    if not rt.confirmed:
        job = {"raw_text": args.raw_text, "title_hint": args.title_hint}
        return await _pending(
            rt,
            "create_job",
            job,
            f"将创建一条岗位并自动解析 JD（原文 {len(args.raw_text)} 字）。",
        )
    return await _request(
        "POST", "/api/jobs", json={"raw_text": args.raw_text, "title_hint": args.title_hint}
    )


async def _delete_job(args: DeleteJobArgs) -> dict[str, Any]:
    rt = current_runtime()
    if not rt.confirmed:
        job = await _request("GET", f"/api/jobs/{args.job_id}")
        return await _pending(
            rt,
            "delete_job",
            {"job_id": args.job_id},
            f"将删除岗位 #{args.job_id}「{job.get('title', '')}」，以及它的原始 JD 文件。此操作不可恢复。",
        )
    return await _request("DELETE", f"/api/jobs/{args.job_id}")


async def _list_claims(args: ListClaimsArgs) -> dict[str, Any]:
    """不带 ledger_id 时汇总所有账本。

    以前默认取第一个账本，而第一个往往是空的，模型就会得出
    "没有待确认证据"的错误结论。个人账本优先排在前面。
    """
    ledgers = await _request("GET", "/api/ledgers")
    if not ledgers:
        return {"claims": [], "total": 0, "note": "还没有证据账本"}

    if args.ledger_id is not None:
        targets = [lg for lg in ledgers if lg["id"] == args.ledger_id]
        if not targets:
            raise ToolFailure(f"账本不存在：{args.ledger_id}")
    else:
        targets = sorted(ledgers, key=lambda lg: (lg.get("kind") != "personal", lg["id"]))

    params = {"status": args.status} if args.status else {}
    rows: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for ledger in targets:
        items = await _request("GET", f"/api/ledgers/{ledger['id']}/claims", params=params)
        summary.append(
            {
                "ledger_id": ledger["id"],
                "name": ledger.get("name", ""),
                "kind": ledger.get("kind", ""),
                "matched": len(items),
            }
        )
        for item in items:
            rows.append({**item, "ledger_id": ledger["id"], "ledger_name": ledger.get("name", "")})

    rows.sort(key=lambda r: r["id"])
    return {
        "ledgers": summary,
        "claims": rows[:30],
        "total": len(rows),
        "pending": sum(1 for r in rows if r.get("status") == "pending"),
        "truncated": len(rows) > 30,
    }


async def _add_claim(args: AddClaimArgs) -> dict[str, Any]:
    ledgers = await _request("GET", "/api/ledgers")
    ledger_id = args.ledger_id
    if ledger_id is None:
        if ledgers:
            ledger_id = ledgers[0]["id"]
        else:
            created = await _request("POST", "/api/ledgers", json={"name": "默认账本"})
            ledger_id = created["id"]
    return await _request(
        "POST",
        f"/api/ledgers/{ledger_id}/claims",
        json={
            "statement": args.statement,
            "kind": args.kind,
            "organization": args.organization,
            "role": args.role,
            "period": args.period,
            "status": "pending",
        },
    )


async def _set_claim_status(args: SetClaimStatusArgs) -> dict[str, Any]:
    return await _request(
        "POST",
        "/api/claims/bulk-status",
        json={"claim_ids": args.claim_ids, "status": args.status},
    )


async def _list_drafts(_: ListDraftsArgs) -> dict[str, Any]:
    rows = await _request("GET", "/api/drafts")
    versions = await _request("GET", "/api/versions")
    return {"drafts": rows, "versions": versions[:10]}


async def _create_draft(args: CreateDraftArgs) -> dict[str, Any]:
    rt = current_runtime()
    if not rt.confirmed:
        return await _pending(
            rt,
            "create_draft",
            args.model_dump(),
            f"将创建一份生成简历「{args.title}」（模板 {args.template}）。",
        )
    body: dict[str, Any] = {"title": args.title, "template": args.template}
    if args.ledger_id is not None:
        body["ledger_id"] = args.ledger_id
    else:
        ledgers = await _request("GET", "/api/ledgers")
        if not ledgers:
            raise ToolFailure("还没有证据账本，请先在证据账本页创建")
        body["ledger_id"] = ledgers[0]["id"]
    if args.target_job_id is not None:
        body["target_job_id"] = args.target_job_id
    return await _request("POST", "/api/drafts", json=body)


async def _set_template(args: SetTemplateArgs) -> dict[str, Any]:
    return await _request("PATCH", f"/api/drafts/{args.draft_id}", json={"template": args.template})


async def _export(args: ExportArgs) -> dict[str, Any]:
    rt = current_runtime()
    if not rt.confirmed:
        draft = await _request("GET", f"/api/drafts/{args.draft_id}")
        gate = draft.get("gate") or {}
        if not gate.get("ok", False):
            errors = gate.get("errors") or []
            raise ToolFailure(
                "证据门禁未通过，无法导出："
                + "；".join(e.get("message", "") for e in errors[:3])
            )
        template = args.template or draft.get("template", "asu")
        return await _pending(
            rt,
            "export_resume",
            {"draft_id": args.draft_id, "template": template},
            f"将导出简历 #{args.draft_id}「{draft.get('title', '')}」为 PDF（模板 {template}），生成一个新版本。",
        )
    params = {"template": args.template} if args.template else {}
    return await _request("POST", f"/api/drafts/{args.draft_id}/export", params=params)


async def _generate(args: DraftIdArgs) -> dict[str, Any]:
    result = await _request("POST", f"/api/drafts/{args.draft_id}/generate")
    await _tag_child(result.get("run_id"))
    return result


async def _evaluate(args: EvaluateArgs) -> dict[str, Any]:
    draft = await _request("GET", f"/api/drafts/{args.draft_id}")
    if not draft.get("resume_json"):
        raise ToolFailure("这份草稿还没有内容，先点「AI 生成初稿」")
    result = await _request("POST", "/api/evaluations", json={"draft_id": args.draft_id})
    await _tag_child(result.get("run_id"))
    return result


async def _list_projects(_: ListProjectsArgs) -> dict[str, Any]:
    rows = await _request("GET", "/api/projects")
    return {"projects": rows}


async def _scan(args: ProjectIdArgs) -> dict[str, Any]:
    result = await _request("POST", f"/api/projects/{args.project_id}/scan")
    await _tag_child(result.get("run_id"))
    return result


async def _guide(args: GuideArgs) -> dict[str, Any]:
    result = await _request(
        "POST", f"/api/projects/{args.project_id}/guide", json={"kind": args.kind}
    )
    await _tag_child(result.get("run_id"))
    return result


async def _list_evaluations(_: Empty) -> dict[str, Any]:
    rows = await _request("GET", "/api/evaluations")
    return {"evaluations": rows[:20], "total": len(rows)}


async def _list_interviews(_: ListInterviewsArgs) -> dict[str, Any]:
    rows = await _request("GET", "/api/interviews")
    return {"interviews": rows[:20], "total": len(rows)}


async def _create_interview(args: InterviewArgs) -> dict[str, Any]:
    if args.draft_id is None and args.imported_resume_id is None:
        raise ToolFailure("请提供 draft_id 或 imported_resume_id")
    body: dict[str, Any] = {"job_id": args.job_id}
    if args.draft_id is not None:
        body.update({"resume_kind": "draft", "draft_id": args.draft_id})
    else:
        body.update({"resume_kind": "imported", "imported_resume_id": args.imported_resume_id})
    return await _request("POST", "/api/interviews", json=body)


async def _ask_user(args: AskUserArgs) -> dict[str, Any]:
    rt = current_runtime()
    rt.cards.append(
        {"type": "ask", "question": args.question, "options": args.options}
    )
    return {"asked": args.question, "options": args.options}


async def _set_plan(args: SetPlanArgs) -> dict[str, Any]:
    """把计划写成全量快照；前端据此打勾。"""
    rt = current_runtime()
    steps = [
        {"id": i + 1, "label": s.label, "status": s.status}
        for i, s in enumerate(args.steps[:12])
    ]
    from app.harness import events as ev
    from app.models.chat import ChatTurn

    turn = await rt.session.get(ChatTurn, rt.turn_id)
    if turn is not None:
        turn.plan = steps
        await rt.session.commit()
    await rt.handle.emit(ev.CHAT_PLAN, {"turn_id": rt.turn_id, "steps": steps})
    return {"steps": steps}


async def _list_runs(args: ListRunsArgs) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": max(1, min(args.limit, 50))}
    if args.status:
        params["status"] = args.status
    if args.kind:
        params["kind"] = args.kind
    rows = await _request("GET", "/api/runs", params=params)
    return {"runs": rows}


async def _get_run(args: RunIdArgs) -> dict[str, Any]:
    run = await _request("GET", f"/api/runs/{args.run_id}")
    events = await _request("GET", f"/api/runs/{args.run_id}/events")
    interesting = [
        e for e in events
        if e.get("type") in ("run.log", "tool.called", "tool.result", "agent.started", "agent.finished", "run.finished")
    ]
    return {
        "run": run,
        "last_events": interesting[-12:],
        "total_events": len(events),
    }


async def _search_events(args: SearchEventsArgs) -> dict[str, Any]:
    params: dict[str, Any] = {"q": args.query, "limit": max(1, min(args.limit, 50))}
    if args.run_ids:
        params["run_ids"] = ",".join(str(i) for i in args.run_ids)
    if args.types:
        params["types"] = ",".join(args.types)
    if args.status:
        params["status"] = args.status
    if args.kind:
        params["kind"] = args.kind
    if args.since:
        params["since"] = args.since
    if args.until:
        params["until"] = args.until
    rows = await _request("GET", "/api/runs/events/search", params=params)
    return {"events": rows, "total": len(rows)}


async def _watch_run(args: WatchRunArgs) -> dict[str, Any]:
    run = await _request("GET", f"/api/runs/{args.run_id}")
    events = await _request("GET", f"/api/runs/{args.run_id}/events", params={"after": args.last_seq})
    events = [e for e in events if e.get("type") != "token.delta"]
    return {
        "run": run,
        "events": events[-20:],
        "still_running": run.get("status") in ("queued", "running"),
        "last_seq": events[-1]["seq"] if events else args.last_seq,
    }


async def _cancel_run(args: CancelRunArgs) -> dict[str, Any]:
    rt = current_runtime()
    if not rt.confirmed:
        run = await _request("GET", f"/api/runs/{args.run_id}")
        return await _pending(
            rt,
            "cancel_run",
            {"run_id": args.run_id},
            f"将取消 Run #{args.run_id}「{run.get('title', '')}」（当前 {run.get('status')}）。",
        )
    return await _request("POST", f"/api/runs/{args.run_id}/cancel")


async def _investigate(args: InvestigateArgs) -> dict[str, Any]:
    rt = current_runtime()
    if rt.investigate is None:
        raise ToolFailure("当前轮次不支持主动调查")
    if rt.investigations >= 1:
        raise ToolFailure(
            "本轮已经调查过一次，不能重复派调查。"
            "请直接用已有结论回答，或改用 list_* / get_* / search_run_events 查询。"
        )
    rt.investigations += 1
    return await rt.investigate(args.question)


async def _pending(
    rt: ChatRuntime, tool: str, args: dict[str, Any], preview: str
) -> dict[str, Any]:
    from app.models.chat import ChatAction

    action = ChatAction(
        session_id=rt.session_id,
        turn_id=rt.turn_id,
        run_id=rt.run_id,
        tool_call_id=rt.tool_call_id,
        tool=tool,
        args=args,
        preview=preview,
        risk="destructive",
        status="pending",
    )
    rt.session.add(action)
    await rt.session.flush()
    await rt.session.commit()

    card = {
        "type": "confirm",
        "action_id": action.id,
        "tool": tool,
        "preview": preview,
    }
    rt.pending.append({"action_id": action.id, "tool": tool, "preview": preview})
    rt.cards.append(card)

    from app.harness import events as ev

    await rt.handle.emit(
        ev.CHAT_CONFIRMATION,
        {"action_id": action.id, "tool": tool, "preview": preview},
    )
    return {
        "status": "pending_confirmation",
        "action_id": action.id,
        "preview": preview,
        "note": "已生成确认卡，用户确认后才会执行。不要重复调用。",
    }


# ---------- 注册 ----------

_CHAT = ("chat",)
_READ_ONLY = ("chat", "investigate")


def _adapt(model: type[BaseModel], handler: Callable[[BaseModel], Awaitable[Any]]):
    """registry 按关键字参数调用 handler；对话工具按参数模型接收。

    这里做一层适配，让两种写法共用同一个注册表。
    """

    async def run(**kwargs):
        return await handler(model.model_validate(kwargs))

    return run


def register_tools() -> None:
    """导入即注册；重复导入要幂等（worker 重新加载模块时）。"""
    from app.harness.registry import ToolSpec

    existing = set(registry.names())

    # (name, description, args_model, handler, risk, card, audiences, timeout_s)
    specs = [
        ("get_overview", "查看工作台整体状态：岗位/简历/证据/项目/评价/面试的计数与待办", Empty, _get_overview, "read", "overview", _CHAT, 30.0),
        ("list_jobs", "列出岗位，可按投递状态筛选", ListJobsArgs, _list_jobs, "read", "jobs", _CHAT, 30.0),
        ("get_job", "查看单个岗位详情（解析结果与原始 JD）", JobIdArgs, _get_job, "read", "job", _READ_ONLY, 30.0),
        ("set_job_status", "设置岗位投递状态：saved/applied/interviewing/rejected", SetJobStatusArgs, _set_job_status, "write", "", _CHAT, 30.0),
        ("create_job", "从用户复制的 JD 原文创建岗位并解析", CreateJobArgs, _create_job, "destructive", "", _CHAT, 30.0),
        ("delete_job", "删除岗位及其原始 JD 文件", DeleteJobArgs, _delete_job, "destructive", "", _CHAT, 30.0),
        ("list_claims", "列出证据条目，可按状态筛选。不传 ledger_id 时汇总所有账本", ListClaimsArgs, _list_claims, "read", "claims", _READ_ONLY, 30.0),
        ("add_claim", "新增一条证据条目（初始为待确认）", AddClaimArgs, _add_claim, "write", "", _CHAT, 30.0),
        ("set_claim_status", "批量设置证据状态：confirmed/rejected/pending", SetClaimStatusArgs, _set_claim_status, "write", "", _CHAT, 30.0),
        ("list_drafts", "列出生成简历草稿与已导出版本", ListDraftsArgs, _list_drafts, "read", "drafts", _READ_ONLY, 30.0),
        ("create_draft", "创建一份新的生成简历草稿", CreateDraftArgs, _create_draft, "destructive", "", _CHAT, 30.0),
        ("set_draft_template", "切换草稿模板", SetTemplateArgs, _set_template, "write", "", _CHAT, 30.0),
        ("generate_resume", "基于已确认证据为草稿生成内容（返回 run_id）", DraftIdArgs, _generate, "slow", "", _CHAT, 30.0),
        ("export_resume", "导出门禁校验后的 PDF 版本", ExportArgs, _export, "destructive", "", _CHAT, 30.0),
        ("evaluate_resume", "发起四 critic 简历评价（返回 run_id）", EvaluateArgs, _evaluate, "slow", "", _CHAT, 30.0),
        ("list_projects", "列出本地项目与扫描状态", ListProjectsArgs, _list_projects, "read", "projects", _READ_ONLY, 30.0),
        ("scan_project", "扫描项目源码提取证据（返回 run_id，可能约 9 分钟）", ProjectIdArgs, _scan, "slow", "", _CHAT, 30.0),
        ("make_project_guide", "为项目生成面经导学（返回 run_id）", GuideArgs, _guide, "slow", "", _CHAT, 30.0),
        ("list_evaluations", "列出简历评价记录", Empty, _list_evaluations, "read", "evaluations", _READ_ONLY, 30.0),
        ("list_interviews", "列出模拟面试会话", ListInterviewsArgs, _list_interviews, "read", "interviews", _READ_ONLY, 30.0),
        ("create_interview", "创建模拟面试会话（之后跳转页面作答）", InterviewArgs, _create_interview, "write", "", _CHAT, 30.0),
        ("ask_user", "向用户提问并给出可选项。当缺少必要信息时使用", AskUserArgs, _ask_user, "read", "ask", _CHAT, 10.0),
        ("set_plan", "更新本轮任务计划。多步任务开始时先列 2-5 步，之后每完成一步重新提交全量计划", SetPlanArgs, _set_plan, "read", "plan", _CHAT, 10.0),
        ("list_runs", "列出 Run，可按状态/类型筛选", ListRunsArgs, _list_runs, "read", "runs", _READ_ONLY, 30.0),
        ("get_run", "查看单个 Run 的状态、token 与最近事件", RunIdArgs, _get_run, "read", "run", _READ_ONLY, 30.0),
        ("search_run_events", "跨 Run 检索事件摘要。回答『之前为什么失败』『哪个 run 做过某操作』时使用", SearchEventsArgs, _search_events, "read", "events", _READ_ONLY, 30.0),
        ("watch_run", "查看某个 Run 的最新事件与是否仍在运行", WatchRunArgs, _watch_run, "read", "run", _READ_ONLY, 30.0),
        ("cancel_run", "取消一个排队中或执行中的 Run", CancelRunArgs, _cancel_run, "destructive", "", _CHAT, 30.0),
        ("investigate", "派只读子 agent 调查大量日志或材料，只回结论", InvestigateArgs, _investigate, "read", "investigation", _CHAT, 420.0),
    ]
    for name, desc, model, handler, risk, card, audiences, timeout_s in specs:
        if name in existing:
            continue
        registry.register(
            ToolSpec(
                name=name,
                description=desc,
                args_model=model,
                handler=_adapt(model, handler),
                read_only=risk == "read",
                risk=risk,
                card=card,
                audiences=audiences,
                timeout_s=timeout_s,
            )
        )


register_tools()


async def call(name: str, raw_args: dict[str, Any], *, run_id: int | None = None) -> Any:
    """经 registry 调用，保留统一的参数校验、超时与错误语义。

    只读工具在同一轮内对相同参数做一次记忆化：模型重复调用时直接回放结果，
    避免它因为"没看清"而反复查询。
    """
    rt = _runtime.get()
    key = ""
    if rt is not None and rt.cache is not None:
        key = f"{name}:{json.dumps(raw_args, ensure_ascii=False, sort_keys=True, default=str)}"
        if key in rt.cache:
            cached = rt.cache[key]
            if isinstance(cached, dict):
                return {**cached, "_cached": True}
            return cached

    try:
        result = await registry.call(name, raw_args, run_id=run_id)
    except ToolError as exc:
        raise ToolFailure(str(exc)) from exc

    if rt is not None and key and registry.get(name).risk == "read":
        rt.cache[key] = result
    return result


def schemas(audience: str = "chat") -> list[dict[str, Any]]:
    return registry.schemas(audience=audience)


def tool_risk(name: str) -> str:
    return registry.get(name).risk
