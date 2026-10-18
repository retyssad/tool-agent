"""扫描安全边界与证据归一化测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.project_pipeline import _normalize_evidence, _normalize_refs
from app.tools.scan import SKIP_DIRS, ScanError, _group_modules, assert_in_roots, scan_project


def test_whitelist_rejects_outside_root(tmp_path: Path, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "project_roots", str(tmp_path))
    with pytest.raises(ScanError, match="不在白名单"):
        assert_in_roots(Path("C:/Windows"))


def test_whitelist_accepts_inside_root(tmp_path: Path, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "project_roots", str(tmp_path))
    sub = tmp_path / "proj"
    sub.mkdir()
    assert assert_in_roots(sub) == sub.resolve()


def test_whitelist_accepts_files_but_not_missing(tmp_path: Path, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "project_roots", str(tmp_path))
    f = tmp_path / "a.py"
    f.write_text("x = 1", encoding="utf-8")
    assert assert_in_roots(f) == f.resolve()
    with pytest.raises(ScanError, match="不存在"):
        assert_in_roots(tmp_path / "missing.py")
    with pytest.raises(ScanError, match="不是目录"):
        assert_in_roots(f, must_be_dir=True)


def test_skip_dirs_cover_dependency_folders():
    for name in ("node_modules", ".venv", "venv", "__pycache__", "dist", "chroma_db"):
        assert name in SKIP_DIRS


def test_scan_ignores_dependencies_and_counts_code(tmp_path: Path, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "project_roots", str(tmp_path))

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("print('hi')\n" * 10, encoding="utf-8")
    (app_dir / "notes.md").write_text("# notes", encoding="utf-8")
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "dep.py").write_text("x = 1\n" * 100, encoding="utf-8")
    modules_dir = tmp_path / "node_modules" / "pkg"
    modules_dir.mkdir(parents=True)
    (modules_dir / "index.js").write_text("var x=1;\n" * 50, encoding="utf-8")

    result = scan_project(tmp_path)
    assert result.file_count == 1
    assert result.total_lines == 10
    assert result.modules[0].name == "app"
    assert any(".venv" in s for s in result.skipped_dirs)


def test_scan_reads_readme(tmp_path: Path, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "project_roots", str(tmp_path))
    (tmp_path / "README.md").write_text("# Demo\nhello", encoding="utf-8")
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    result = scan_project(tmp_path)
    assert "Demo" in result.readme


def test_scan_skips_oversized_file(tmp_path: Path, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "project_roots", str(tmp_path))
    (tmp_path / "big.py").write_text("x" * 250_000, encoding="utf-8")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    result = scan_project(tmp_path)
    assert result.file_count == 1


def test_evidence_requires_valid_refs():
    items = [
        {"statement": "有引用", "refs": [{"path": "a.py", "line": 3}], "category": "feature"},
        {"statement": "无引用", "refs": [], "category": "feature"},
        {"statement": "路径非法", "refs": [{"path": "../etc/passwd", "line": 1}]},
        {"statement": "行号为零", "refs": [{"path": "a.py", "line": 0}]},
    ]
    result = _normalize_evidence(items)
    assert [r["statement"] for r in result] == ["有引用"]


def test_evidence_dedupes_and_caps():
    items = [
        {"statement": "同一条", "refs": [{"path": "a.py", "line": 1}]},
        {"statement": "同一条", "refs": [{"path": "a.py", "line": 2}]},
    ] + [
        {"statement": f"条目{i}", "refs": [{"path": "a.py", "line": i}]} for i in range(40)
    ]
    result = _normalize_evidence(items)
    assert len(result) == 24
    assert len({r["statement"] for r in result}) == 24


def test_evidence_invalid_category_falls_back():
    result = _normalize_evidence(
        [{"statement": "x", "refs": [{"path": "a.py", "line": 1}], "category": "bogus"}]
    )
    assert result[0]["category"] == "feature"


def test_normalize_refs_limits_to_four():
    refs = [{"path": f"f{i}.py", "line": i + 1} for i in range(10)]
    assert len(_normalize_refs(refs)) == 4


def test_group_modules_orders_by_lines(tmp_path: Path):
    from app.tools.scan import FileInfo

    files = [
        FileInfo("a/x.py", "a/x.py", 100, 1000, "python"),
        FileInfo("a/y.py", "a/y.py", 50, 500, "python"),
        FileInfo("b/z.ts", "b/z.ts", 10, 100, "typescript"),
    ]
    modules = _group_modules(files, tmp_path)
    assert [m.name for m in modules] == ["a", "b"]
    assert modules[0].lines == 150
