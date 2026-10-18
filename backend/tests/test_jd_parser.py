"""JD 解析的回归测试：不依赖模型，覆盖 schema 校验与容错。"""
from __future__ import annotations

import pytest

from app.agents.jd_parser import _load_json, _validate


VALID = {
    "title": "后端工程师",
    "company": "",
    "location": "杭州",
    "salary": "25-40K",
    "seniority": "3-5年",
    "employment_type": "",
    "responsibilities": ["负责服务开发"],
    "hard_requirements": ["3 年后端经验"],
    "skills": ["Java", "MySQL"],
    "nice_to_have": [],
    "keywords": ["后端", "杭州"],
    "summary": "杭州后端岗位",
    "ambiguities": [],
}


def test_load_json_strips_markdown_fence():
    data = _load_json('```json\n{"title": "x"}\n```')
    assert data["title"] == "x"


def test_load_json_rejects_invalid():
    with pytest.raises(ValueError, match="不是合法 JSON"):
        _load_json("这不是 JSON")


def test_validate_normalizes_missing_fields():
    data = {"title": "后端工程师", "responsibilities": ["做事"]}
    _validate(data)
    assert data["company"] == ""
    assert data["skills"] == []
    assert data["ambiguities"] == []


def test_validate_rejects_missing_title():
    data = dict(VALID, title="")
    with pytest.raises(ValueError, match="缺少岗位名称"):
        _validate(data)


def test_validate_rejects_empty_content():
    data = dict(VALID, responsibilities=[], hard_requirements=[])
    with pytest.raises(ValueError, match="缺少职责与要求"):
        _validate(data)


def test_validate_rejects_wrong_type():
    data = dict(VALID, skills="Java")
    with pytest.raises(ValueError, match="应为数组"):
        _validate(data)


def test_validate_strips_blank_items():
    data = dict(VALID, skills=["Java", "  ", "", "MySQL"])
    _validate(data)
    assert data["skills"] == ["Java", "MySQL"]
