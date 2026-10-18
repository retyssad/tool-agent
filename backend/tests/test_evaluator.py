"""评价结果后处理测试：score 钳制、where 归一化、汇总。"""
from __future__ import annotations

from app.agents.evaluator import (
    CriticResult,
    _clamp_score,
    _normalize_findings,
    aggregate,
    normalize_where,
)


def test_clamp_score_bounds():
    assert _clamp_score(120) == 100
    assert _clamp_score(-5) == 0
    assert _clamp_score("72.6") == 73
    assert _clamp_score(None) == 0


def test_where_accepts_structured_paths():
    assert normalize_where("experience[0].bullets[1]") == "experience[0].bullets[1]"
    assert normalize_where("skills[4]") == "skills[4]"
    assert normalize_where("profile.headline") == "profile.headline"
    assert normalize_where("__missing__") == "__missing__"


def test_where_strips_text_suffix():
    assert normalize_where("skills[4].text") == "skills[4]"
    assert normalize_where("experience[0].bullets[1].text") == "experience[0].bullets[1]"


def test_where_rejects_garbage():
    assert normalize_where("整份简历") == ""
    assert normalize_where("resume.json") == ""
    assert normalize_where("") == ""


def test_normalize_findings_filters_and_caps():
    raw = [
        {"title": f"问题{i}", "severity": "high", "where": "skills[0].text"} for i in range(12)
    ] + [{"severity": "high"}, {"title": "有效", "severity": "weird"}]
    result = _normalize_findings(raw, "clarity")
    assert len(result) == 8
    assert all(item["severity"] == "high" for item in result)
    assert result[0]["where"] == "skills[0]"


def test_normalize_findings_defaults_severity():
    result = _normalize_findings([{"title": "x", "severity": "unknown"}], "jd_match")
    assert result[0]["severity"] == "low"


def _result(key: str, score: int, findings=None, error: str = "") -> CriticResult:
    return CriticResult(
        key=key, name=key, score=score, findings=findings or [], summary="", error=error
    )


def test_aggregate_averages_only_successful():
    results = [
        _result("a", 80),
        _result("b", 60),
        _result("c", 0, error="boom"),
    ]
    report = aggregate(results)
    assert report["overall_score"] == 70
    assert report["summary"]["failed_critics"] == ["c"]
    assert report["dimension_scores"] == {"a": 80, "b": 60, "c": 0}


def test_aggregate_orders_high_first():
    results = [
        _result(
            "a",
            70,
            [
                {"critic": "a", "severity": "low", "title": "低", "detail": "", "where": "", "excerpt": "", "suggestion": "", "claim_ids": []},
                {"critic": "a", "severity": "high", "title": "高", "detail": "", "where": "", "excerpt": "", "suggestion": "", "claim_ids": []},
            ],
        )
    ]
    report = aggregate(results)
    assert report["summary"]["priorities"][0]["title"] == "高"


def test_aggregate_all_failed_gives_zero():
    report = aggregate([_result("a", 0, error="x"), _result("b", 0, error="y")])
    assert report["overall_score"] == 0
    assert report["summary"]["failed_critics"] == ["a", "b"]
