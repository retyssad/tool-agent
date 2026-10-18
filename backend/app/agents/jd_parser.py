"""JD 解析：单 agent + JSON 输出。产物写入 jobs.parsed。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from app.harness import events as ev
from app.harness.executor import RunHandle
from app.models.job import Job, RawJD
from app.tasks import register

PARSE_SYSTEM_PROMPT = """你是中文招聘 JD 的结构化解析器。只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释。

字段定义：
- title: 岗位名称，原文有就照抄
- company: 公司名。原文没有就留空字符串，绝对不要猜测或补全
- location: 城市/地区，取原文
- salary: 薪资区间原文，如 "25-40K"
- seniority: 经验要求原文，如 "3-5年"
- employment_type: 全职/实习/兼职，未写明留空
- responsibilities: 岗位职责，逐条拆分，保留原文措辞
- hard_requirements: 硬性要求（学历、年限、必须掌握的技能、必须的行业经验）
- skills: 技能栈，规范化后的短词，如 "MySQL" 而不是 "熟悉 MySQL"
- nice_to_have: 加分项 / 优先条件
- keywords: 用于检索的关键词，包含岗位名、技能、城市、级别
- summary: 两句话以内的岗位摘要，只复述原文信息
- ambiguities: 原文中不明确、需要用户确认的点，没有就给空数组

规则：
1. 不要发明原文没有的信息。缺失字段用空字符串或空数组。
2. responsibilities 与 hard_requirements 不要重复同一句。
3. skills 只放技术名词或明确的方法论名词，不要放"沟通能力强"这类软技能。
"""


@register("parse_job")
async def parse_job(handle: RunHandle) -> dict[str, Any]:
    from app.db import SessionLocal

    job_id = int(handle.target.get("job_id") or 0)
    if not job_id:
        raise ValueError("缺少 job_id")

    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise ValueError(f"岗位不存在: {job_id}")
        raw = await session.scalar(
            select(RawJD).where(RawJD.job_id == job_id).order_by(RawJD.id.desc())
        )
        if raw is None:
            raise ValueError("该岗位没有原始 JD")

        # 解析失败时把原因写回岗位，前端可直接看到
        try:
            return await _parse_inner(handle, session, job, raw)
        except Exception as exc:
            job.parse_status = "failed"
            job.parse_error = f"{type(exc).__name__}: {exc}"[:500]
            await session.commit()
            raise


async def _parse_inner(handle: RunHandle, session, job: Job, raw: RawJD) -> dict[str, Any]:
    prompt_hash = await handle.use_prompt(
        "parse_job.system", PARSE_SYSTEM_PROMPT, "JD 结构化解析"
    )
    job.parse_status = "running"
    await session.commit()

    await handle.log(f"开始解析岗位 #{job.id}，原文 {len(raw.content)} 字")

    reply = await handle.call_model(
        [
            {"role": "system", "content": PARSE_SYSTEM_PROMPT},
            {"role": "user", "content": raw.content},
        ],
        agent="jd_parser",
        prompt_name="parse_job.system",
        prompt_hash=prompt_hash,
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    parsed = _load_json(reply.content)
    _validate(parsed)

    job.parsed = parsed
    job.parse_status = "succeeded"
    job.parse_error = ""
    job.parse_run_id = handle.run_id
    # 只有原文明确解析出的字段才回填，绝不猜测
    if parsed.get("title"):
        job.title = str(parsed["title"])[:200]
    if parsed.get("company"):
        job.company = str(parsed["company"])[:200]
    if parsed.get("location"):
        job.location = str(parsed["location"])[:100]
    if parsed.get("salary"):
        job.salary = str(parsed["salary"])[:100]
    if parsed.get("seniority"):
        job.seniority = str(parsed["seniority"])[:50]
    await session.commit()

    await handle.emit(
        ev.ARTIFACT_CREATED,
        {"kind": "job_parsed", "job_id": job.id, "fields": sorted(parsed)},
    )
    return {"job_id": job.id, "title": job.title, "skills": len(parsed.get("skills", []))}


REQUIRED_LIST_FIELDS = (
    "responsibilities",
    "hard_requirements",
    "skills",
    "nice_to_have",
    "keywords",
    "ambiguities",
)
REQUIRED_STR_FIELDS = (
    "title",
    "company",
    "location",
    "salary",
    "seniority",
    "employment_type",
    "summary",
)


def _load_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        # 容错：模型偶尔包代码块
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出不是合法 JSON: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return data


def _validate(data: dict[str, Any]) -> None:
    """规范化为固定 schema；缺字段补空，类型不对就报错。"""
    for key in REQUIRED_STR_FIELDS:
        value = data.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"字段 {key} 应为字符串")
        data[key] = value.strip()

    for key in REQUIRED_LIST_FIELDS:
        value = data.get(key, [])
        if value is None:
            value = []
        if not isinstance(value, list):
            raise ValueError(f"字段 {key} 应为数组")
        data[key] = [str(item).strip() for item in value if str(item).strip()]

    if not data["title"]:
        raise ValueError("解析结果缺少岗位名称")
    if not data["responsibilities"] and not data["hard_requirements"]:
        raise ValueError("解析结果缺少职责与要求，无法使用")
