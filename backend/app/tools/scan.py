"""项目源码扫描。

安全边界：路径必须落在配置的 project_roots 白名单内；只读；硬性跳过依赖目录与二进制。
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings

# 绝不扫描的目录
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
    "target", "out", "bin", "obj", ".next", ".nuxt", "coverage", ".tox", "site-packages",
    "chroma_db", "data", "assets", "logs", "tmp", ".cache",
}

CODE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".java", ".kt", ".rs",
    ".cs", ".rb", ".php", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".sql",
}
DOC_EXTS = {".md", ".txt", ".toml", ".yaml", ".yml", ".json"}
SKIP_FILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock",
    "Cargo.lock", "go.sum",
}

MAX_FILE_BYTES = 200_000
MAX_FILES = 4000
MAX_MODULES = 12
MAX_FILES_PER_MODULE = 8
# 单个模块送进模型的字符预算：中文代码约 1 字符 1 token，
# 14k 字符会让模型输出上千 token 的 JSON，容易触发 300 秒超时
MODULE_CHAR_BUDGET = 8_000
# 模块行数超过此值时再按下一层目录拆分，避免单模块过大
MODULE_SPLIT_LINES = 2_500


@dataclass(slots=True)
class FileInfo:
    path: str  # 相对项目根
    abs_path: str
    lines: int
    size: int
    lang: str


@dataclass(slots=True)
class ModuleInfo:
    name: str
    files: list[FileInfo] = field(default_factory=list)
    lines: int = 0
    lang: str = ""


@dataclass(slots=True)
class ScanResult:
    root: str
    file_count: int
    module_count: int
    modules: list[ModuleInfo]
    readme: str
    docs: list[dict[str, str]]
    git: dict[str, object]
    skipped_dirs: list[str]
    total_lines: int

    def as_dict(self) -> dict:
        return {
            "root": self.root,
            "file_count": self.file_count,
            "module_count": self.module_count,
            "total_lines": self.total_lines,
            "modules": [
                {
                    "name": m.name,
                    "lines": m.lines,
                    "lang": m.lang,
                    "files": [
                        {"path": f.path, "lines": f.lines, "size": f.size, "lang": f.lang}
                        for f in m.files
                    ],
                }
                for m in self.modules
            ],
            "readme": self.readme,
            "docs": self.docs,
            "git": self.git,
            "skipped_dirs": self.skipped_dirs,
        }


class ScanError(RuntimeError):
    pass


def assert_in_roots(path: Path, *, must_be_dir: bool = False) -> Path:
    """白名单校验放在工具层，不交给模型判断。

    文件与目录都允许；需要目录时显式传 must_be_dir。
    """
    settings = get_settings()
    resolved = path.expanduser().resolve()
    roots = settings.project_root_list
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ScanError(f"路径不在白名单内: {resolved}（白名单: {[str(r) for r in roots]}）")
    if not resolved.exists():
        raise ScanError(f"路径不存在: {resolved}")
    if must_be_dir and not resolved.is_dir():
        raise ScanError(f"不是目录: {resolved}")
    return resolved


def _lang(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".vue": "vue",
        ".go": "go", ".java": "java", ".kt": "kotlin", ".rs": "rust",
        ".sql": "sql", ".md": "markdown",
    }.get(ext, ext.lstrip("."))


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def scan_project(root: str | Path) -> ScanResult:
    base = assert_in_roots(Path(root), must_be_dir=True)
    skipped: list[str] = []
    files: list[FileInfo] = []
    docs: list[dict[str, str]] = []
    readme = ""

    for dirpath, dirnames, filenames in os.walk(base):
        # 原地过滤，os.walk 就不会进入这些目录
        keep = []
        for name in dirnames:
            if name in SKIP_DIRS or name.startswith("."):
                skipped.append(str(Path(dirpath, name).relative_to(base)))
            else:
                keep.append(name)
        dirnames[:] = keep

        for name in filenames:
            path = Path(dirpath, name)
            if name in SKIP_FILES or name.startswith("."):
                continue
            ext = path.suffix.lower()
            if ext not in CODE_EXTS and ext not in DOC_EXTS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                skipped.append(f"{path.relative_to(base)} (too large)")
                continue
            rel = str(path.relative_to(base)).replace("\\", "/")
            if ext in CODE_EXTS:
                files.append(FileInfo(rel, str(path), _count_lines(path), size, _lang(path)))
            elif name.lower().startswith("readme") and not readme:
                readme = path.read_text(encoding="utf-8", errors="replace")[:6000]
            elif ext == ".md":
                docs.append({"path": rel, "excerpt": path.read_text(encoding="utf-8", errors="replace")[:800]})
            if len(files) >= MAX_FILES:
                break

    modules = _group_modules(files, base)
    return ScanResult(
        root=str(base),
        file_count=len(files),
        module_count=len(modules),
        modules=modules,
        readme=readme,
        docs=docs[:10],
        git=_git_info(base),
        skipped_dirs=skipped[:60],
        total_lines=sum(f.lines for f in files),
    )


def _group_modules(files: list[FileInfo], base: Path) -> list[ModuleInfo]:
    """按目录聚合；过大的目录再拆一层，避免单模块读不完。"""
    buckets: dict[str, list[FileInfo]] = {}
    for f in files:
        parts = Path(f.path).parts
        # 取前两层目录，单文件项目归到根
        key = "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else ".")
        buckets.setdefault(key, []).append(f)

    # 大模块细分到第三层
    expanded: dict[str, list[FileInfo]] = {}
    for name, items in buckets.items():
        if sum(i.lines for i in items) <= MODULE_SPLIT_LINES:
            expanded[name] = items
            continue
        splits: dict[str, list[FileInfo]] = {}
        for f in items:
            parts = Path(f.path).parts
            sub = "/".join(parts[:3]) if len(parts) > 3 else name
            splits.setdefault(sub, []).append(f)
        expanded.update(splits)

    modules = [
        ModuleInfo(
            name=name,
            files=sorted(items, key=lambda x: -x.lines)[:MAX_FILES_PER_MODULE],
            lines=sum(i.lines for i in items),
            lang=max({i.lang for i in items}, key=lambda l: sum(i.lines for i in items if i.lang == l)),
        )
        for name, items in expanded.items()
    ]
    modules.sort(key=lambda m: -m.lines)
    return modules[:MAX_MODULES]


def _git_info(base: Path) -> dict[str, object]:
    """只允许只读 git 查询，禁止任意 shell。"""
    if not (base / ".git").exists():
        return {"is_repo": False}
    try:
        def run(*args: str) -> str:
            out = subprocess.run(
                ["git", "-C", str(base), *args],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            return out.stdout.strip() if out.returncode == 0 else ""

        return {
            "is_repo": True,
            "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "last_commit": run("log", "-1", "--pretty=%h %ad %s", "--date=short"),
            "commit_count": run("rev-list", "--count", "HEAD"),
            "recent_files": run("log", "-5", "--pretty=%h %s").splitlines(),
            "diff_stat": run("diff", "--stat", "HEAD~5..HEAD")[:1200],
        }
    except (OSError, subprocess.SubprocessError):
        return {"is_repo": True, "error": "git 命令不可用"}


def read_module_files(
    module: ModuleInfo,
    *,
    char_budget: int = MODULE_CHAR_BUDGET,
    max_files: int = 6,
) -> list[dict[str, str]]:
    """读取模块内代码。

    先给每个文件分配均等预算，保证覆盖多个文件；剩余预算再补给大文件。
    这样避免一个大文件吃光预算，导致模块里其他文件完全没被看到。
    """
    candidates: list[tuple[Path, str, int, str]] = []
    for info in module.files:
        path = Path(info.abs_path)
        if not path.is_file():
            continue
        try:
            assert_in_roots(path)
        except ScanError:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        candidates.append((path, info.path, info.lines, text))

    if not candidates:
        return []

    selected = candidates[:max_files]
    per_file = max(1_500, char_budget // len(selected))
    out: list[dict[str, str]] = []
    used = 0
    for index, (_path, rel, lines, text) in enumerate(selected):
        remaining_budget = char_budget - used
        remaining_files = len(selected) - index
        allowance = min(per_file, max(remaining_budget - 1_500 * (remaining_files - 1), 1_500))
        if allowance <= 0:
            break
        body = text[:allowance]
        truncated = len(text) > allowance
        if truncated:
            body += "\n…（已截断，原文件更长）"
        out.append({"path": rel, "content": body, "lines": lines, "truncated": truncated})
        used += len(body)
        if used >= char_budget:
            break
    return out
