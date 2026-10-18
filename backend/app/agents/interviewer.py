"""模拟面试：计划、出题、评分、报告。

角色分离（ADR-0003）：
- planner：一次性生成问题契约（阶段、题目、出题依据）
- interviewer：逐轮出题与追问，不评分
- evaluator：只看问答，独立评分，不参与提问
"""
from __future__ import annotations

import json
from typing import Any

from app.harness.executor import RunHandle

PLANNER_PROMPT = """你是面试规划官。基于简历、目标岗位 JD 与项目证据，设计一场 10 道主问题的面试契约。

只输出 JSON：
{
  "focus": "本场面试的核心考察方向，一句话",
  "dimensions": ["事实一致性", "技术深度", "岗位匹配", "表达结构", "追问韧性"],
  "questions": [
    {
      "stage": "project|job_fit|behavior|closing",
      "question": "完整问题",
      "intent": "这题想验证什么",
      "basis": {"claims": [1,2], "evidence_ids": [3], "jd": "对应的 JD 要求原文"},
      "difficulty": 1-5,
      "follow_ups": ["可能的追问方向，2-3 条"]
    }
  ]
}

规则：
1. 阶段配比：project 4 题（深挖项目与简历中的技术细节），job_fit 3 题（JD 硬性要求与职责），behavior 2 题，closing 1 题。
2. 每题必须能落到简历里的具体 bullet 或项目证据；basis.claims 只引用给出的 claim id。
3. 问题要具体到「你怎么做 X」而不是「谈谈你对 X 的理解」。
4. 不要问简历里没有的信息；如果某项 JD 要求简历未覆盖，问「你如何理解/如何补足」，不要假设有经验。
5. 由浅入深，同一主题不要连续重复。
"""

INTERVIEWER_PROMPT = """你是面试官。根据面试契约和已经问过的内容，判断下一个问题应该问什么。

只输出 JSON：
{
  "action": "ask|follow_up|finish",
  "question": "要问的问题（action=finish 时留空）",
  "stage": "project|job_fit|behavior|closing",
  "reason": "为什么现在问这个",
  "follow_up_of": 上一题的 index（仅 follow_up 时填，否则 0）
}

推进规则（必须遵守）：
1. **主问题优先推进**。追问只用于两种情况：回答明显空泛/回避，或与简历证据冲突。回答质量正常时必须问下一道主问题。
2. **每题最多追问 1 次**。当前连续追问次数 >= 1 时，必须 action=ask 推进下一道主问题。
3. **按契约顺序**：project 阶段的题问完后必须进入 job_fit，然后 behavior，最后 closing。不要一直停留在 project。
4. 已经追问过的主题不要再追问；已经问过的题目不要重复。
5. 追问必须锚定候选人刚才的回答原话，并且要求他补充**新信息**（具体做法、数据、边界），不要只是换个说法重问。
6. 所有主问题问完后 action=finish。
7. 不要在追问里复述候选人已回答的内容再让他重答。
"""

EVALUATOR_PROMPT = """你是独立的面试评分官。只评估候选人对**这一道题**的回答，不参与提问。

只输出 JSON：
{
  "scores": {
    "fact_consistency": 1-5,
    "technical_depth": 1-5,
    "job_match": 1-5,
    "structure": 1-5
  },
  "evidence": {
    "fact_consistency": "与简历/项目证据是否一致，指出冲突点",
    "technical_depth": "是否讲清实现、取舍、边界",
    "job_match": "是否命中 JD 要求",
    "structure": "是否清楚、有边界、不空泛"
  },
  "red_flags": ["明显问题，没有就空数组"],
  "one_line": "一句话点评"
}

评分标准：
- 5：有具体实现、量化结果、清晰边界，可直接追问三层
- 4：主体清楚，缺一层细节
- 3：方向对，但停留在概念
- 2：空泛或答非所问
- 1：错误或与简历矛盾

硬性规则：
1. 事实一致性优先：回答与简历证据冲突时，该项不得高于 2 分，并在 red_flags 里写明冲突。
2. 不知道就说不知道并给出推理，可得 3 分；编造技术细节得 1 分。
3. 只依据本题与给定上下文，不要参考其他题的回答。
"""

REPORT_PROMPT = """你是面试复盘官。基于整场问答与逐题评分，输出最终报告。

只输出 JSON：
{
  "overall_score": 0-100,
  "dimension_scores": {
    "fact_consistency": 0-100,
    "technical_depth": 0-100,
    "job_match": 0-100,
    "structure": 0-100,
    "resilience": 0-100
  },
  "verdict": "strong_hire|hire|borderline|no_hire",
  "summary": "3-5 句整体判断",
  "strengths": ["做得好的具体表现，引用题目或回答"],
  "weaknesses": ["具体短板，不要泛泛而谈"],
  "red_flags": ["需要你自己核实或补证据的地方"],
  "next_steps": ["下一步应该怎么准备，按优先级排序，3-5 条"],
  "question_review": [
    {"idx": 1, "question": "题目", "score": 0-5, "comment": "简评"}
  ]
}

规则：
1. 分数要有依据，与逐题评分整体一致；不要因为某题特别好就拉高整体。
2. resilience 由追问表现判断：被追问后是否更具体、是否崩。
3. verdict 与 overall_score 对齐：>=85 strong_hire，>=70 hire，>=55 borderline，否则 no_hire。
4. next_steps 必须可执行，指出该补哪类证据或练哪类表达。
"""


async def make_plan(
    handle: RunHandle,
    *,
    resume: dict[str, Any],
    job: dict[str, Any],
    claims: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt_hash = await handle.use_prompt("interview.plan", PLANNER_PROMPT, "面试契约规划")
    reply = await handle.call_model(
        [
            {"role": "system", "content": PLANNER_PROMPT},
            {
                "role": "user",
                "content": (
                    f"目标岗位：\n{json.dumps(job, ensure_ascii=False)}\n\n"
                    f"简历：\n{json.dumps(resume, ensure_ascii=False)}\n\n"
                    f"已确认证据（可引用 id）：\n"
                    + "\n".join(f"[{c['id']}] {c['statement']}" for c in claims[:40])
                    + "\n\n项目证据（可引用 id）：\n"
                    + "\n".join(f"[{e['id']}] {e['statement']}" for e in evidence[:30])
                ),
            },
        ],
        agent="interview.planner",
        prompt_name="interview.plan",
        prompt_hash=prompt_hash,
        temperature=0.3,
        response_format={"type": "json_object"},
        stream=True,
    )
    data = _load(reply.content)
    return _normalize_plan(data, claims, evidence)


async def next_question(
    handle: RunHandle,
    *,
    pact: dict[str, Any],
    turns: list[dict[str, Any]],
    follow_up_streak: int,
) -> dict[str, Any]:
    prompt_hash = await handle.use_prompt("interview.ask", INTERVIEWER_PROMPT, "面试官出题")
    transcript = "\n".join(
        f"[{t['idx']}] ({t['stage']}/{t['kind']}) Q: {t['question']}\nA: {(t['answer'] or '（未作答）')[:600]}"
        for t in turns[-8:]
    )
    asked = sum(1 for t in turns if t.get("kind") == "primary")
    contract = pact.get("questions") or []
    next_primary = contract[asked] if asked < len(contract) else None
    reply = await handle.call_model(
        [
            {"role": "system", "content": INTERVIEWER_PROMPT},
            {
                "role": "user",
                "content": (
                    f"面试契约（共 {len(contract)} 道主问题，已问 {asked} 道）：\n"
                    f"{json.dumps(pact, ensure_ascii=False)}\n\n"
                    f"最近问答：\n{transcript or '（尚未开始）'}\n\n"
                    f"当前连续追问次数：{follow_up_streak}（>=1 时必须推进主问题）\n\n"
                    f"下一道待问主问题：\n"
                    + (
                        f"[{asked + 1}] ({next_primary.get('stage')}) {next_primary.get('question')}"
                        if next_primary
                        else "（主问题已问完，只剩收尾或追问）"
                    )
                ),
            },
        ],
        agent="interview.interviewer",
        prompt_name="interview.ask",
        prompt_hash=prompt_hash,
        temperature=0.45,
        response_format={"type": "json_object"},
    )
    data = _load(reply.content)
    action = str(data.get("action") or "ask")
    if action not in {"ask", "follow_up", "finish"}:
        action = "ask"
    return {
        "action": action,
        "question": str(data.get("question") or "").strip()[:1000],
        "stage": str(data.get("stage") or "project")[:40],
        "reason": str(data.get("reason") or "")[:300],
        "follow_up_of": int(data.get("follow_up_of") or 0),
    }


async def score_answer(
    handle: RunHandle,
    *,
    question: str,
    answer: str,
    resume: dict[str, Any],
    job: dict[str, Any],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt_hash = await handle.use_prompt("interview.score", EVALUATOR_PROMPT, "逐答评分")
    reply = await handle.call_model(
        [
            {"role": "system", "content": EVALUATOR_PROMPT},
            {
                "role": "user",
                "content": (
                    f"岗位：{json.dumps(job, ensure_ascii=False)}\n\n"
                    f"简历与证据：\n{json.dumps(resume, ensure_ascii=False)[:6000]}\n"
                    + "\n".join(f"[{c['id']}] {c['statement']}" for c in claims[:30])
                    + f"\n\n问题：{question}\n\n回答：{answer}"
                ),
            },
        ],
        agent="interview.evaluator",
        prompt_name="interview.score",
        prompt_hash=prompt_hash,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    data = _load(reply.content)
    return _normalize_scores(data)


async def final_report(
    handle: RunHandle,
    *,
    session_meta: dict[str, Any],
    turns: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt_hash = await handle.use_prompt("interview.report", REPORT_PROMPT, "面试终局报告")
    transcript = "\n\n".join(
        f"[{t['idx']}] {t['stage']}/{t['kind']}\nQ: {t['question']}\n"
        f"A: {t['answer'] or '（未作答）'}\n"
        f"逐题评分: {json.dumps(t['scores'], ensure_ascii=False) if t['scores'] else '（跳过）'}\n"
        f"面试官反馈: {t['feedback'] or '无'}"
        for t in turns
    )
    reply = await handle.call_model(
        [
            {"role": "system", "content": REPORT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"会话信息：{json.dumps(session_meta, ensure_ascii=False)}\n\n"
                    f"完整问答：\n{transcript[:40000]}"
                ),
            },
        ],
        agent="interview.reporter",
        prompt_name="interview.report",
        prompt_hash=prompt_hash,
        temperature=0.2,
        response_format={"type": "json_object"},
        stream=True,
    )
    return _normalize_report(_load(reply.content))


# ---------- 归一化 ----------

ALLOWED_STAGES = {"project", "job_fit", "behavior", "closing"}


def _normalize_plan(
    data: dict[str, Any],
    claims: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    valid_claims = {c["id"] for c in claims}
    valid_evidence = {e["id"] for e in evidence}
    questions: list[dict[str, Any]] = []
    for raw in data.get("questions") or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("question") or "").strip()
        if not text:
            continue
        stage = str(raw.get("stage") or "project")
        if stage not in ALLOWED_STAGES:
            stage = "project"
        basis = raw.get("basis") or {}
        if not isinstance(basis, dict):
            basis = {}
        basis_claims = []
        for cid in basis.get("claims") or []:
            try:
                value = int(cid)
            except (TypeError, ValueError):
                continue
            if value in valid_claims:
                basis_claims.append(value)
        basis_evidence = []
        for eid in basis.get("evidence_ids") or []:
            try:
                value = int(eid)
            except (TypeError, ValueError):
                continue
            if value in valid_evidence:
                basis_evidence.append(value)
        try:
            difficulty = max(1, min(5, int(raw.get("difficulty") or 3)))
        except (TypeError, ValueError):
            difficulty = 3
        questions.append(
            {
                "stage": stage,
                "question": text[:1000],
                "intent": str(raw.get("intent") or "")[:400],
                "basis": {
                    "claims": basis_claims,
                    "evidence_ids": basis_evidence,
                    "jd": str(basis.get("jd") or "")[:400],
                },
                "difficulty": difficulty,
                "follow_ups": [str(f)[:300] for f in (raw.get("follow_ups") or [])][:3],
            }
        )
    if not questions:
        raise ValueError("面试计划为空：模型没有产出可用问题")
    stages = {q["stage"] for q in questions}
    return {
        "focus": str(data.get("focus") or "")[:300],
        "dimensions": [str(d)[:40] for d in (data.get("dimensions") or [])][:8]
        or ["事实一致性", "技术深度", "岗位匹配", "表达结构", "追问韧性"],
        "questions": questions[:12],
        "stages": sorted(stages),
    }


SCORE_KEYS = ("fact_consistency", "technical_depth", "job_match", "structure")


def _normalize_scores(data: dict[str, Any]) -> dict[str, Any]:
    raw_scores = data.get("scores") or {}
    scores: dict[str, int] = {}
    for key in SCORE_KEYS:
        try:
            value = int(round(float(raw_scores.get(key, 0))))
        except (TypeError, ValueError):
            value = 0
        scores[key] = max(1, min(5, value))
    evidence = data.get("evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {}
    return {
        "scores": scores,
        "evidence": {k: str(evidence.get(k) or "")[:400] for k in SCORE_KEYS},
        "red_flags": [str(x)[:200] for x in (data.get("red_flags") or [])][:5],
        "one_line": str(data.get("one_line") or "")[:300],
    }


VERDICTS = {"strong_hire", "hire", "borderline", "no_hire"}


def _normalize_report(data: dict[str, Any]) -> dict[str, Any]:
    try:
        overall = max(0, min(100, int(round(float(data.get("overall_score") or 0)))))
    except (TypeError, ValueError):
        overall = 0

    dims_raw = data.get("dimension_scores") or {}
    dims: dict[str, int] = {}
    for key in (*SCORE_KEYS, "resilience"):
        try:
            value = int(round(float(dims_raw.get(key, 0))))
        except (TypeError, ValueError):
            value = 0
        dims[key] = max(0, min(100, value))

    verdict = str(data.get("verdict") or "")
    if verdict not in VERDICTS:
        verdict = (
            "strong_hire" if overall >= 85
            else "hire" if overall >= 70
            else "borderline" if overall >= 55
            else "no_hire"
        )

    review = []
    for raw in data.get("question_review") or []:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw.get("idx") or 0)
        except (TypeError, ValueError):
            continue
        try:
            score = max(0, min(5, int(round(float(raw.get("score") or 0)))))
        except (TypeError, ValueError):
            score = 0
        review.append(
            {
                "idx": idx,
                "question": str(raw.get("question") or "")[:300],
                "score": score,
                "comment": str(raw.get("comment") or "")[:400],
            }
        )

    return {
        "overall_score": overall,
        "dimension_scores": dims,
        "verdict": verdict,
        "summary": str(data.get("summary") or "")[:1500],
        "strengths": [str(x)[:300] for x in (data.get("strengths") or [])][:6],
        "weaknesses": [str(x)[:300] for x in (data.get("weaknesses") or [])][:6],
        "red_flags": [str(x)[:300] for x in (data.get("red_flags") or [])][:6],
        "next_steps": [str(x)[:300] for x in (data.get("next_steps") or [])][:6],
        "question_review": review[:20],
    }


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
        raise ValueError(f"模型输出不是合法 JSON: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return data
