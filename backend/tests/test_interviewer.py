"""面试 agent 归一化测试：计划、评分、报告的边界处理。"""
from __future__ import annotations

import pytest

from app.agents.interviewer import (
    _normalize_plan,
    _normalize_report,
    _normalize_scores,
)


def _claims():
    return [{"id": 1, "statement": "重构交易链路"}, {"id": 2, "statement": "设计缓存"}]


def _evidence():
    return [{"id": 5, "statement": "实现了 SSE 进度"}]


def test_plan_filters_invalid_claim_and_evidence_ids():
    data = {
        "focus": "后端深度",
        "questions": [
            {
                "stage": "project",
                "question": "你怎么做的？",
                "basis": {"claims": [1, 999], "evidence_ids": [5, 77], "jd": "要求 X"},
                "difficulty": 4,
            }
        ],
    }
    pact = _normalize_plan(data, _claims(), _evidence())
    q = pact["questions"][0]
    assert q["basis"]["claims"] == [1]
    assert q["basis"]["evidence_ids"] == [5]
    assert q["difficulty"] == 4


def test_plan_rejects_empty():
    with pytest.raises(ValueError, match="计划为空"):
        _normalize_plan({"questions": []}, _claims(), _evidence())


def test_plan_normalizes_stage_and_caps_questions():
    data = {
        "questions": [
            {"stage": "weird", "question": f"问题{i}", "difficulty": 9} for i in range(20)
        ]
    }
    pact = _normalize_plan(data, _claims(), _evidence())
    assert len(pact["questions"]) == 12
    assert pact["questions"][0]["stage"] == "project"
    assert pact["questions"][0]["difficulty"] == 5


def test_scores_clamped_to_1_5():
    result = _normalize_scores(
        {
            "scores": {
                "fact_consistency": 9,
                "technical_depth": 0,
                "job_match": "3.4",
                "structure": None,
            },
            "red_flags": ["编造指标"],
            "one_line": "还行",
        }
    )
    assert result["scores"]["fact_consistency"] == 5
    assert result["scores"]["technical_depth"] == 1
    assert result["scores"]["job_match"] == 3
    assert result["scores"]["structure"] == 1
    assert result["red_flags"] == ["编造指标"]


def test_report_verdict_falls_back_to_score():
    report = _normalize_report({"overall_score": 88, "verdict": "unknown"})
    assert report["overall_score"] == 88
    assert report["verdict"] == "strong_hire"

    report = _normalize_report({"overall_score": 60})
    assert report["verdict"] == "borderline"

    report = _normalize_report({"overall_score": 30})
    assert report["verdict"] == "no_hire"


def test_report_clamps_dimensions_and_review():
    report = _normalize_report(
        {
            "overall_score": 120,
            "dimension_scores": {"fact_consistency": -5, "resilience": 140},
            "question_review": [
                {"idx": 1, "question": "q", "score": 9, "comment": "c"},
                {"idx": "x", "score": 3},
            ],
        }
    )
    assert report["overall_score"] == 100
    assert report["dimension_scores"]["fact_consistency"] == 0
    assert report["dimension_scores"]["resilience"] == 100
    assert report["dimension_scores"]["technical_depth"] == 0
    assert len(report["question_review"]) == 1
    assert report["question_review"][0]["score"] == 5


def test_interviewer_accepts_plain_dict_turns(monkeypatch):
    """出题函数接收的是 dict 列表，不能按 ORM 属性访问。"""
    import asyncio
    from types import SimpleNamespace

    from app.agents import interviewer as module

    captured = {}

    class FakeHandle:
        async def use_prompt(self, *args, **kwargs):
            return "hash"

        async def call_model(self, messages, **kwargs):
            captured["user"] = messages[-1]["content"]
            return SimpleNamespace(
                content='{"action":"ask","question":"下一题","stage":"job_fit","reason":"推进"}'
            )

    turns = [
        {"idx": 1, "stage": "project", "kind": "primary", "question": "Q1", "answer": "A1"},
        {"idx": 2, "stage": "project", "kind": "follow_up", "question": "Q2", "answer": "A2"},
    ]
    result = asyncio.run(
        module.next_question(
            FakeHandle(),
            pact={"questions": [{"stage": "project", "question": "Q1"}]},
            turns=turns,
            follow_up_streak=1,
        )
    )
    assert result["action"] == "ask"
    assert "已问 1 道" in captured["user"]
    assert ">=1 时必须推进主问题" in captured["user"]
