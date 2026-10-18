"""简历撰写后处理的回归测试：教育字段兜底、非法 claim 过滤。"""
from __future__ import annotations

from types import SimpleNamespace

from app.agents.resume_writer import _is_school_restatement, _major_from_statement, _merge_education, _normalize


def _claim(cid: int, kind: str, statement: str, org: str = "", role: str = "", period: str = ""):
    return SimpleNamespace(
        id=cid, kind=kind, statement=statement,
        organization=org, role=role, period=period,
    )


def _profile():
    return SimpleNamespace(
        name="张三", headline="后端", location="杭州", phone="1", email="a@b.c",
        wechat="", english_level="", github="", birth_year="", political_status="",
    )


def test_education_restored_when_model_returns_nothing():
    claims = [_claim(9, "education", "在浙江大学就读计算机科学与技术本科。", "浙江大学", "", "2015 - 2019")]
    result = _merge_education([], claims)
    assert result == [
        {
            "school": "浙江大学",
            "major_degree": "计算机科学与技术本科",
            "dates": "2015 - 2019",
            "bullets": [],
        }
    ]


def test_major_extraction_from_statement():
    assert _major_from_statement("在浙江大学就读计算机科学与技术本科。", "浙江大学") == "计算机科学与技术本科"
    assert _major_from_statement("无关陈述", "浙江大学") == ""


def test_school_restatement_detection():
    assert _is_school_restatement("在浙江大学就读计算机科学与技术本科。")
    assert not _is_school_restatement("GPA 3.9，专业前 10%")


def test_normalize_drops_unknown_claim_ids():
    data = {
        "profile": {"headline": "后端"},
        "experience": [
            {
                "organization": "某公司",
                "role": "后端",
                "dates": "2021",
                "bullets": [
                    {"label": "x", "text": "有效", "claim_ids": [1]},
                    {"label": "y", "text": "伪造", "claim_ids": [999]},
                    {"label": "z", "text": "混入", "claim_ids": [1, 999]},
                ],
            }
        ],
        "projects": [],
        "skills": [],
        "campus": [],
        "summary": [],
        "education": [],
    }
    result = _normalize(data, [_claim(1, "experience", "有效", "某公司")], _profile())
    bullets = result["experience"][0]["bullets"]
    assert [b["text"] for b in bullets] == ["有效", "混入"]
    assert bullets[0]["claim_ids"] == [1]
    assert bullets[1]["claim_ids"] == [1]


def test_normalize_drops_empty_entries():
    data = {
        "profile": {},
        "experience": [{"organization": "某公司", "bullets": []}],
        "projects": [],
        "skills": [],
        "campus": [],
        "summary": [],
        "education": [],
    }
    result = _normalize(data, [], _profile())
    assert result["experience"] == []
