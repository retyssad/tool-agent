"""基于已确认证据生成 resume.json。模型只做重组，不得引入新事实。"""
from __future__ import annotations

import json
from typing import Any

from app.harness.executor import RunHandle
from app.models.resume import Claim

WRITE_PROMPT = """你是中文技术简历撰写器。你只能使用给定的「证据条目」写作，不得引入任何新事实。

只输出 JSON 对象，结构如下：
{
  "profile": {"name","headline"},
  "education": [{"school","major_degree","dates","bullets":[{"label","text","claim_ids":[]}]}],
  "experience": [{"organization","role","dates","bullets":[{"label","text","claim_ids":[]}]}],
  "projects": [{"name","summary","dates","bullets":[{"label","text","claim_ids":[]}]}],
  "skills": [{"label","text","claim_ids":[]}],
  "campus": [{"organization","role","dates","bullets":[{"label","text","claim_ids":[]}]}],
  "summary": [{"label","text","claim_ids":[]}]
}

硬性规则：
1. 每个 bullet / skills / summary 条目都必须带非空 claim_ids，只能引用输入里给出的 claim id。
2. 你可以重组措辞、合并同类证据、调整顺序，但不得新增数字、公司、技术栈、成果或职责。
3. 一条 bullet 如果由多条证据合并而来，必须列出全部来源 claim id。
4. 空章节输出空数组，不要编造占位内容。
5. label 用于「核心链路：」这类前缀，没有就留空字符串。
6. 面向目标岗位 JD 重排优先级：JD 命中度高的证据放前面，但不得删改事实。
7. 不要输出 markdown，不要解释，只输出 JSON。
8. **summary 要写，但不能逐条复述**：输出 2-3 句概括，说明这些经历共同证明了什么能力，并标注所有来源 claim id。措辞必须与已有 bullet 不同，禁止原样搬运某条 bullet。
9. **不要复述结构化字段**：学校、专业、时间已经由 school / major_degree / dates 字段承载，bullets 里不要再写「专业：计算机科学与技术本科」这类重复句。教育条目的 bullets 只在有 GPA、排名、主修课程、奖项等额外信息时才填。
10. 如果某个章节没有合适证据，就输出空数组。宁可少写，不要凑内容。
"""


async def write_resume(
    handle: RunHandle,
    *,
    profile: Any,
    claims: list[Claim],
    job: Any | None,
    template: str,
) -> dict[str, Any]:
    prompt_hash = await handle.use_prompt("resume.write", WRITE_PROMPT, "基于证据生成简历")

    claim_lines = []
    for c in claims:
        meta = " / ".join(x for x in (c.organization, c.role, c.period) if x)
        claim_lines.append(
            f"[{c.id}] ({c.kind}{' · ' + meta if meta else ''}) {c.statement}"
        )
    claim_text = "\n".join(claim_lines)

    profile_text = "（未填写）"
    if profile is not None:
        profile_text = json.dumps(
            {
                "name": profile.name,
                "headline": profile.headline,
                "location": profile.location,
                "phone": profile.phone,
                "email": profile.email,
                "wechat": profile.wechat,
                "english_level": profile.english_level,
                "github": profile.github,
                "birth_year": profile.birth_year,
                "political_status": profile.political_status,
            },
            ensure_ascii=False,
        )

    job_text = "（无目标岗位，按通用技术岗优化）"
    if job is not None:
        parsed = job.parsed or {}
        job_text = json.dumps(
            {
                "title": job.title,
                "company": job.company,
                "hard_requirements": parsed.get("hard_requirements", []),
                "skills": parsed.get("skills", []),
                "responsibilities": parsed.get("responsibilities", []),
            },
            ensure_ascii=False,
        )

    reply = await handle.call_model(
        [
            {"role": "system", "content": WRITE_PROMPT},
            {
                "role": "user",
                "content": (
                    f"候选人基本信息：{profile_text}\n\n"
                    f"目标岗位：{job_text}\n\n"
                    f"证据条目（只能引用方括号里的 id）：\n{claim_text}"
                ),
            },
        ],
        agent="resume_writer",
        prompt_name="resume.write",
        prompt_hash=prompt_hash,
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    data = _load(reply.content)
    return _normalize(data, claims, profile)


def _normalize(data: dict[str, Any], claims: list[Claim], profile: Any) -> dict[str, Any]:
    valid_ids = {c.id for c in claims}

    def clean_bullets(items: Any) -> list[dict[str, Any]]:
        out = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            ids = []
            for raw in item.get("claim_ids") or []:
                try:
                    cid = int(raw)
                except (TypeError, ValueError):
                    continue
                if cid in valid_ids:
                    ids.append(cid)
            if not ids:
                # 引用全部非法：模型编造了内容，直接丢弃而不是留空引用
                continue
            out.append(
                {
                    "label": str(item.get("label") or "").strip(),
                    "text": text,
                    "claim_ids": ids,
                }
            )
        return out

    def clean_entries(section: str, items: Any) -> list[dict[str, Any]]:
        out = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            entry: dict[str, Any] = {
                "dates": str(item.get("dates") or "").strip(),
                "bullets": clean_bullets(item.get("bullets")),
            }
            if section == "education":
                entry["school"] = str(item.get("school") or "").strip()
                entry["major_degree"] = str(item.get("major_degree") or "").strip()
            elif section == "projects":
                entry["name"] = str(item.get("name") or "").strip()
                entry["summary"] = str(item.get("summary") or "").strip()
            else:
                entry["organization"] = str(item.get("organization") or "").strip()
                entry["role"] = str(item.get("role") or "").strip()
            if entry["bullets"]:
                out.append(entry)
        return out

    def clean_lines(items: Any) -> list[dict[str, Any]]:
        out = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            ids = []
            for raw in item.get("claim_ids") or []:
                try:
                    cid = int(raw)
                except (TypeError, ValueError):
                    continue
                if cid in valid_ids:
                    ids.append(cid)
            if not ids:
                continue
            out.append(
                {
                    "label": str(item.get("label") or "").strip(),
                    "text": text,
                    "claim_ids": ids,
                }
            )
        return out

    profile_out = {
        "name": (profile.name if profile else "") or "",
        "headline": str((data.get("profile") or {}).get("headline") or "").strip()
        or (profile.headline if profile else ""),
        "location": profile.location if profile else "",
        "phone": profile.phone if profile else "",
        "email": profile.email if profile else "",
        "wechat": profile.wechat if profile else "",
        "english_level": profile.english_level if profile else "",
        "github": profile.github if profile else "",
        "birth_year": profile.birth_year if profile else "",
        "political_status": profile.political_status if profile else "",
    }

    return {
        "schema_version": 1,
        "profile": profile_out,
        "education": _merge_education(clean_entries("education", data.get("education")), claims),
        "experience": clean_entries("experience", data.get("experience")),
        "projects": clean_entries("projects", data.get("projects")),
        "skills": clean_lines(data.get("skills")),
        "campus": clean_entries("campus", data.get("campus")),
        "summary": clean_lines(data.get("summary")),
    }


def _merge_education(entries: list[dict[str, Any]], claims: list[Claim]) -> list[dict[str, Any]]:
    """教育章节的客观字段必须来自 education 类证据，不能依赖模型输出。

    模型可能只返回一条被过滤掉的 bullet，导致整个教育经历丢失；
    这里按证据补齐 school / major_degree / period。
    """
    if entries:
        return entries
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for c in claims:
        if c.kind != "education":
            continue
        school = c.organization or ""
        key = (school, c.period or "")
        entry = out.setdefault(
            key,
            {
                "school": school,
                "major_degree": c.role or "",
                "dates": c.period or "",
                "bullets": [],
            },
        )
        if not entry["major_degree"]:
            entry["major_degree"] = _major_from_statement(c.statement, school)
        text = c.statement.strip()
        if _is_school_restatement(text):
            continue  # 纯字段复述，不重复展示
        entry["bullets"].append({"label": "", "text": text, "claim_ids": [c.id]})
    return [e for e in out.values() if e["school"] or e["bullets"]]





def _major_from_statement(statement: str, school: str) -> str:
    """从「在浙江大学就读计算机科学与技术本科」里取出「计算机科学与技术本科」。"""
    import re

    pattern = rf"在\s*{re.escape(school)}\s*(?:就读|攻读|学习)\s*(.+?)[。.]?$"
    match = re.search(pattern, statement)
    return match.group(1).strip() if match else ""


def _is_school_restatement(text: str) -> bool:
    return ("就读" in text or "攻读" in text) and len(text) <= 40


def _load(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出不是合法 JSON: {text[:160]}") from exc
    if not isinstance(data, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return data
