"""简历评价：四个独立 critic 并行 + 汇总。

每个 critic 只看一个维度，避免互相锚定；who 在哪里、什么问题、怎么改，
全部落到结构化 findings，前端据此高亮定位。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from app.harness import events as ev
from app.harness.executor import RunHandle

CRITIC_DEFS: dict[str, dict[str, str]] = {
    "jd_match": {
        "name": "岗位匹配",
        "prompt": """你是招聘方视角的简历匹配评审。只评估「简历与目标岗位 JD 的匹配度」，不评价文笔。

评分 0-100，标准：
- 硬性要求（学历、年限、必须技能）是否逐条覆盖
- 核心职责是否有对应经历
- 缺失的关键词是否真的缺失（不是换个说法）

输出 JSON：
{"score": 0-100, "findings": [{
  "severity": "high|medium|low|good",
  "title": "短标题，不超过 20 字",
  "detail": "具体说明，引用 JD 中的哪条要求",
  "where": "resume.json 路径，如 experience[0].bullets[1] / skills[2] / profile.headline / __missing__",
  "excerpt": "简历原文片段（不超过 80 字），缺失项留空",
  "suggestion": "可执行的修改建议，不要编造候选人没有的经历"
}], "summary": "两句话结论"}

规则：
1. 只依据给定 JD 与简历，不要假设 JD 之外的行业标准。
2. 硬性要求缺失必须标 high；加分项缺失标 low 或不提。
3. 不要建议编造经历；缺失项的建议应该是"补充真实经历"或"调整表达以体现已有经历"。
4. where 必须精确指向 resume.json 的现有路径；整体性缺失用 "__missing__"。
5. findings 最多 8 条。""",
    },
    "quantification": {
        "name": "成果量化",
        "prompt": """你是成果量化评审。只评估「经历描述是否有可验证的量化结果」，不评价匹配度。

评分 0-100，标准：
- 是否有明确的指标、规模、比例、耗时变化
- 数字是否具体到可信（避免"大幅提升""显著优化"）
- 是否说清个人贡献边界（做了什么 vs 团队做了什么）
- 数字之间是否自相矛盾

输出 JSON：
{"score": 0-100, "findings": [{
  "severity": "high|medium|low|good",
  "title": "短标题",
  "detail": "指出缺什么、为什么重要",
  "where": "resume.json 路径",
  "excerpt": "简历原文片段",
  "suggestion": "补什么类型的数字。只能建议补真实数据，不能虚构具体数值"
}], "summary": "两句话结论"}

规则：
1. 只评量化与可验证性，不评岗位匹配、不评技术深度。
2. 有数字但口径不清（如"提升了性能"却没基线）标 medium。
3. 建议里禁止给出具体虚构数值，只能说明"补充基线/口径/个人贡献比例"。
4. findings 最多 8 条。""",
    },
    "technical_depth": {
        "name": "技术深度",
        "prompt": """你是技术面试官视角的简历评审。只评估「技术描述是否体现了足够的深度与可追问性」，不评价岗位匹配与文笔。

评分 0-100，标准：
- 是否说明技术选型与取舍理由
- 是否体现对原理、边界、失败场景的理解
- 是否只是罗列技术栈而没有实现细节
- 面试官能否顺着这条描述追问出 3 层

输出 JSON：
{"score": 0-100, "findings": [{
  "severity": "high|medium|low|good",
  "title": "短标题",
  "detail": "指出深度不足在哪一层",
  "where": "resume.json 路径",
  "excerpt": "简历原文片段",
  "suggestion": "建议补充哪类细节（如取舍、边界、失败处理），但不能编造技术事实"
}], "summary": "两句话结论"}

规则：
1. 不要求每条都写原理，但核心项目至少要有一条能追问到实现层。
2. "熟悉 X"这类罗列标 medium；有场景、有取舍、有结果的标 good。
3. 禁止建议添加简历中未出现过的技术。
4. findings 最多 8 条。""",
    },
    "clarity": {
        "name": "表达与结构",
        "prompt": """你是简历编辑评审。只评估「表达的清晰度与结构」，不评价岗位匹配与技术深度。

评分 0-100，标准：
- 每条要点是否一句话说清动作 + 结果
- 是否有重复表述、空泛形容词、主语混乱
- 层级是否清晰（时间、公司、角色、职责）
- 篇幅是否合理，有没有可以合并或删掉的条目

输出 JSON：
{"score": 0-100, "findings": [{
  "severity": "high|medium|low|good",
  "title": "短标题",
  "detail": "具体问题",
  "where": "resume.json 路径",
  "excerpt": "简历原文片段",
  "suggestion": "改写方向，不改变事实"
}], "summary": "两句话结论"}

规则：
1. 只评表达，不改事实。
2. 同义重复、同一结论出现在两个章节，标 medium。
3. 每条要点超过 3 行或塞入两件事，标 medium。
4. findings 最多 8 条。""",
    },
}


@dataclass(slots=True)
class CriticResult:
    key: str
    name: str
    score: int
    findings: list[dict[str, Any]]
    summary: str
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "critic": self.key,
            "name": self.name,
            "score": self.score,
            "findings": self.findings,
            "summary": self.summary,
            "error": self.error,
        }


async def run_critic(
    handle: RunHandle,
    *,
    key: str,
    resume: dict[str, Any],
    job: dict[str, Any],
    claims_digest: str,
) -> CriticResult:
    spec = CRITIC_DEFS[key]
    prompt_hash = await handle.use_prompt(f"evaluate.{key}", spec["prompt"], f"{spec['name']}评审")
    try:
        reply = await handle.call_model(
            [
                {"role": "system", "content": spec["prompt"]},
                {
                    "role": "user",
                    "content": (
                        f"目标岗位：\n{json.dumps(job, ensure_ascii=False)}\n\n"
                        f"证据摘要（用于判断哪些内容有据可依）：\n{claims_digest}\n\n"
                        f"简历 JSON：\n{json.dumps(resume, ensure_ascii=False)}"
                    ),
                },
            ],
            agent=f"critic.{key}",
            prompt_name=f"evaluate.{key}",
            prompt_hash=prompt_hash,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        data = _load(reply.content)
        return CriticResult(
            key=key,
            name=spec["name"],
            score=_clamp_score(data.get("score")),
            findings=_normalize_findings(data.get("findings"), key),
            summary=str(data.get("summary") or "")[:500],
        )
    except Exception as exc:  # noqa: BLE001
        # 单个 critic 失败不影响其他维度
        return CriticResult(
            key=key,
            name=spec["name"],
            score=0,
            findings=[],
            summary="",
            error=f"{type(exc).__name__}: {exc}"[:300],
        )


async def run_all_critics(
    handle: RunHandle,
    *,
    resume: dict[str, Any],
    job: dict[str, Any],
    claims_digest: str,
) -> list[CriticResult]:
    """四个 critic 并行。网络调用并发，库写由 RunHandle 的锁串行化。"""
    await handle.emit(
        ev.RUN_LOG,
        {"message": f"启动 {len(CRITIC_DEFS)} 个并行 critic", "level": "info"},
    )
    tasks = [
        run_critic(handle, key=key, resume=resume, job=job, claims_digest=claims_digest)
        for key in CRITIC_DEFS
    ]
    return list(await asyncio.gather(*tasks))


def aggregate(results: list[CriticResult]) -> dict[str, Any]:
    """汇总：等权平均，失败的维度不计入。"""
    ok = [r for r in results if not r.error]
    scores = {r.key: r.score for r in results}
    overall = round(sum(r.score for r in ok) / len(ok)) if ok else 0

    severity_rank = {"high": 0, "medium": 1, "low": 2, "good": 3}
    ranked = sorted(
        (f for r in ok for f in r.findings),
        key=lambda f: severity_rank.get(f["severity"], 4),
    )
    high = [f for f in ranked if f["severity"] == "high"]
    good = [f for f in ranked if f["severity"] == "good"]

    return {
        "overall_score": overall,
        "dimension_scores": scores,
        "summary": {
            "conclusion": _conclusion(overall),
            "strengths": [f"{f['title']}：{f['detail']}" for f in good[:4]],
            "priorities": [
                {"title": f["title"], "suggestion": f["suggestion"], "where": f["where"]}
                for f in (high or ranked)[:5]
            ],
            "failed_critics": [r.key for r in results if r.error],
        },
        "results": [r.as_dict() for r in results],
    }


def _conclusion(score: int) -> str:
    if score >= 85:
        return "整体质量较高，可直接投递，重点打磨个别措辞。"
    if score >= 70:
        return "基础扎实，存在若干明确改进点，建议按优先项修改后再投。"
    if score >= 50:
        return "有明显短板，建议先补齐关键缺口再投递。"
    return "当前版本与目标岗位差距较大，建议重写核心经历或考虑其他岗位。"


def _clamp_score(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, number))


SEVERITIES = {"high", "medium", "low", "good"}

# where 只允许指向结构化位置，去掉模型偶尔附带的 .text / .label 后缀
_WHERE_RE = re.compile(
    r"^(?:__missing__|profile\.[a-z_]+|"
    r"(?:education|experience|projects|skills|campus|summary)\[\d+\](?:\.bullets\[\d+\])?)$"
)


def normalize_where(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    value = re.sub(r"\.(text|label|claim_ids)$", "", value)
    if value in {"", "resume", "resume.json"}:
        return ""
    return value if _WHERE_RE.match(value) else ""


def _normalize_findings(items: Any, critic: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        severity = str(item.get("severity") or "low").strip()
        if severity not in SEVERITIES:
            severity = "low"
        claim_ids: list[int] = []
        for raw in item.get("claim_ids") or []:
            try:
                claim_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        out.append(
            {
                "critic": critic,
                "severity": severity,
                "title": title[:200],
                "detail": str(item.get("detail") or "")[:1500],
                "where": normalize_where(str(item.get("where") or "")),
                "excerpt": str(item.get("excerpt") or "")[:300],
                "suggestion": str(item.get("suggestion") or "")[:800],
                "claim_ids": claim_ids[:8],
            }
        )
    return out[:8]


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
