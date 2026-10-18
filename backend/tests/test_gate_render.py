"""门禁与渲染的回归测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools import gate
from app.tools.render import render


def _claim(cid: int, status: str = "confirmed", ledger_id: int = 1):
    return SimpleNamespace(id=cid, status=status, ledger_id=ledger_id)


def _resume(bullet_ids: list[int]) -> dict:
    return {
        "profile": {"name": "张三"},
        "experience": [
            {
                "organization": "某公司",
                "role": "后端",
                "dates": "2021-至今",
                "bullets": [{"label": "核心", "text": "重构交易链路", "claim_ids": bullet_ids}],
            }
        ],
        "projects": [],
        "skills": [],
        "summary": [],
        "education": [],
        "campus": [],
    }


def test_gate_passes_with_confirmed_claims():
    result = gate.check(_resume([1]), [_claim(1)])
    assert result.ok
    assert result.errors == []


def test_gate_rejects_missing_claim_ids():
    result = gate.check(_resume([]), [_claim(1)])
    assert not result.ok
    assert result.errors[0]["code"] == "missing_claim_ids"


def test_gate_rejects_unconfirmed_claim():
    result = gate.check(_resume([2]), [_claim(2, status="pending")])
    assert not result.ok
    assert result.errors[0]["code"] == "unconfirmed_claim"
    assert result.errors[0]["status"] == "pending"


def test_gate_rejects_unknown_claim():
    result = gate.check(_resume([99]), [_claim(1)])
    assert not result.ok
    assert result.errors[0]["code"] == "unknown_claim"


def test_gate_rejects_bad_id_type():
    result = gate.check(_resume(["abc"]), [_claim(1)])
    assert not result.ok
    assert result.errors[0]["code"] == "bad_claim_id"


def test_gate_rejects_missing_name():
    resume = _resume([1])
    resume["profile"] = {"name": ""}
    result = gate.check(resume, [_claim(1)])
    assert any(e["code"] == "missing_name" for e in result.errors)


def test_gate_requires_claim_ids_on_skills_and_summary():
    resume = _resume([1])
    resume["skills"] = [{"label": "", "text": "熟悉 Python", "claim_ids": []}]
    result = gate.check(resume, [_claim(1)])
    assert not result.ok
    assert any(e["where"].startswith("skills") for e in result.errors)


@pytest.mark.parametrize("template", ["asu", "tpl-01", "tpl-05", "tpl-08", "tpl-13", "tpl-16", "tpl-18"])
def test_render_is_self_contained(template: str):
    html = render(_resume([1]), template)
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert 'href="' not in html.split("<body")[0]  # 不依赖外部样式表
    assert "张三" in html


def test_render_template_layers():
    """侧栏模板必须产生 layout-sidebar，双栏模板必须产生 layout-two-column。"""
    def markup(template: str) -> str:
        return render(_resume([1]), template).split("<body", 1)[1]

    assert 'class="layout-sidebar"' in markup("tpl-05")
    assert 'class="layout-two-column"' in markup("tpl-08")
    assert "layout-sidebar" not in markup("tpl-01")


def test_render_escapes_html():
    resume = _resume([1])
    resume["experience"][0]["bullets"][0]["text"] = "<script>alert(1)</script>"
    html = render(resume, "asu")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_unknown_template_raises():
    with pytest.raises(ValueError, match="未知模板"):
        render(_resume([1]), "nope")
