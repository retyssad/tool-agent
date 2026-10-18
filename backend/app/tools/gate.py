"""resume.json 校验门禁。

规则（ADR-0002）：
- 每个 bullet 必须携带非空 claim_ids
- 引用的 claim 必须存在于同一账本
- 引用的 claim 状态必须是 confirmed
- 客观字段（学校/公司/时间）不绑 claim，但不能为空到无法识别
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.common import CLAIM_CONFIRMED
from app.models.resume import Claim


@dataclass(slots=True)
class GateResult:
    ok: bool
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": self.errors}


BULLET_SECTIONS = ("experience", "projects", "campus")
TEXT_SECTIONS = ("skills", "summary")


def _iter_bullets(resume: dict[str, Any]):
    """产出 (section, entry_index, bullet_index, bullet)。"""
    for section in BULLET_SECTIONS:
        for ei, entry in enumerate(resume.get(section) or []):
            for bi, bullet in enumerate(entry.get("bullets") or []):
                yield section, ei, bi, bullet
    for section in TEXT_SECTIONS:
        for ei, entry in enumerate(resume.get(section) or []):
            yield section, ei, None, entry


def check(resume: dict[str, Any], claims: list[Claim]) -> GateResult:
    by_id = {c.id: c for c in claims}
    errors: list[dict[str, Any]] = []

    if not (resume.get("profile") or {}).get("name"):
        errors.append({"code": "missing_name", "message": "缺少候选人姓名"})

    for section, ei, bi, item in _iter_bullets(resume):
        where = f"{section}[{ei}]" + (f".bullets[{bi}]" if bi is not None else "")
        text = (item.get("text") or "").strip()
        if not text:
            errors.append({"code": "empty_text", "where": where, "message": "内容为空"})
            continue

        ids = item.get("claim_ids") or []
        if not isinstance(ids, list) or not ids:
            errors.append(
                {
                    "code": "missing_claim_ids",
                    "where": where,
                    "message": "该条内容没有绑定证据条目",
                    "text": text[:80],
                }
            )
            continue

        for raw_id in ids:
            try:
                claim_id = int(raw_id)
            except (TypeError, ValueError):
                errors.append(
                    {"code": "bad_claim_id", "where": where, "message": f"非法 claim id: {raw_id!r}"}
                )
                continue

            claim = by_id.get(claim_id)
            if claim is None:
                errors.append(
                    {
                        "code": "unknown_claim",
                        "where": where,
                        "claim_id": claim_id,
                        "message": f"证据条目 #{claim_id} 不在本账本中",
                    }
                )
                continue
            if claim.status != CLAIM_CONFIRMED:
                errors.append(
                    {
                        "code": "unconfirmed_claim",
                        "where": where,
                        "claim_id": claim_id,
                        "status": claim.status,
                        "message": f"证据条目 #{claim_id} 未确认（{claim.status}）",
                    }
                )

    return GateResult(ok=not errors, errors=errors)


def summarize_issues(result: GateResult) -> str:
    if result.ok:
        return "校验通过"
    return "；".join(e["message"] for e in result.errors[:5])
