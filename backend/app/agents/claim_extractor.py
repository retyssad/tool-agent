"""从导入简历文本提炼证据条目。单 agent，只读，返回结构化结果。"""
from __future__ import annotations

import json
from typing import Any

from app.harness.executor import RunHandle

EXTRACT_PROMPT = """你是简历事实提炼器。从给定的简历文本中，提取可以被「单独验证」的事实条目。

只输出 JSON 对象：{"claims": [...]}。每条结构：
{
  "statement": "一句完整、可独立理解的事实陈述，保留原文中的数字与专有名词",
  "kind": "experience | project | education | skill | campus | award | other",
  "organization": "公司或学校名，原文没有就留空",
  "role": "职位或角色，原文没有就留空",
  "period": "时间区间原文，如 2021.03-至今，没有就留空",
  "tags": ["技术或领域关键词"]
}

规则：
1. 一条事实一条 claim。不要把两件不同的事写进一条。
2. 数字、指标、技术栈、职责边界必须来自原文，禁止推测或补全。
3. 技能类可以合并成一条有边界的陈述，例如「熟悉 MySQL、Redis、Kafka」，但不要加入原文未提的技能。
4. 自我评价、主观形容词不要单独成条，除非包含可验证的具体事实。
5. 如果某条已经在「已有条目」中出现（语义相同），跳过它，不要重复。
6. 最多 40 条，按重要性排序。
7. statement 不要出现「负责」「参与」以外的夸大词；原文写什么就写什么。
8. **不要提取联系方式和个人信息**：姓名、电话、邮箱、城市、年龄、政治面貌、英语等级、GitHub 等都不是可写进简历的证据，由档案单独维护。
9. **不要拆条**：动作和它的结果必须写在同一句里。例如「重构交易链路，支撑日均 300 万订单，P99 从 820ms 降至 210ms」是一条，不要拆成「负责重构」和「P99 下降」两条。
10. **不要写「负责X」这种没有结果的空陈述**：如果原文只有职责没有结果，把职责、技术实现、结果合并成一条完整陈述；实在没有可验证内容就不提取。
11. **不要提取时间信息作为独立条目**：时间属于 organization/period 字段，不要写成「项目时间为 2023.05-2024.01」这种句子。
12. kind 为 experience 的条目必须带 organization；kind 为 project 的必须带 organization（项目名）。"""


async def extract_claims_from_resume(
    handle: RunHandle,
    *,
    text: str,
    existing_statements: list[str],
    source_ref: str,
) -> list[dict[str, Any]]:
    prompt_hash = await handle.use_prompt("claim.extract", EXTRACT_PROMPT, "从简历提炼证据条目")
    existing = "\n".join(f"- {s}" for s in existing_statements[:80]) or "（无）"

    reply = await handle.call_model(
        [
            {"role": "system", "content": EXTRACT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"来源：{source_ref}\n\n"
                    f"已有条目：\n{existing}\n\n"
                    f"简历文本：\n{text[:12000]}"
                ),
            },
        ],
        agent="claim_extractor",
        prompt_name="claim.extract",
        prompt_hash=prompt_hash,
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    data = _load(reply.content)
    items = data.get("claims") or []
    if not isinstance(items, list):
        raise ValueError("claims 字段必须是数组")

    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        statement = str(raw.get("statement") or "").strip()
        if not statement or statement in seen:
            continue
        seen.add(statement)
        kind = str(raw.get("kind") or "other").strip()
        if kind not in {"experience", "project", "education", "skill", "campus", "award", "other"}:
            kind = "other"
        # 个人信息与空陈述不作为证据
        if kind == "other":
            continue
        if _looks_like_contact(statement):
            continue
        organization = str(raw.get("organization") or "")[:200]
        if kind == "project" and not organization:
            continue
        cleaned.append(
            {
                "statement": statement[:600],
                "kind": kind,
                "organization": organization,
                "role": str(raw.get("role") or "")[:200],
                "period": str(raw.get("period") or "")[:100],
                "tags": [str(t)[:50] for t in (raw.get("tags") or [])][:12],
            }
        )
    return cleaned[:40]


_CONTACT_MARKERS = ("联系电话", "联系邮箱", "手机号", "姓名为", "所在地为", "微信", "政治面貌")


def _looks_like_contact(statement: str) -> bool:
    return any(marker in statement for marker in _CONTACT_MARKERS)


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
