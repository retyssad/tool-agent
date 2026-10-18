"""模板目录。

18 套模板共用 `assets/frame/base.css`，差异全部来自 body 的变体类与章节容器布局。
因此渲染器只负责按 schema 生成标记，模板选择等价于选择（变体类, 布局, 章节顺序）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FRAME_DIR = REPO_ROOT / "skill库" / "assets" / "frame"
ASU_DIR = FRAME_DIR / "asu"


@dataclass(slots=True)
class TemplateMeta:
    key: str
    name: str
    # body class 列表，对应 assets/frame/base.css 中的 variant-*
    body_classes: list[str]
    # flow：单列；sidebar：侧栏 + 主栏；two-column：两列流式
    layout: str = "flow"
    # 章节实际顺序（首字段为标题，第二个为英文副标题，可为空）
    sections: list[tuple[str, str]] = field(default_factory=list)
    # 侧栏容纳的章节 key（sidebar 布局使用）
    sidebar_keys: list[str] = field(default_factory=list)
    two_page: bool = False
    description: str = ""


DEFAULT_SECTIONS: list[tuple[str, str]] = [
    ("education", "教育背景"),
    ("experience", "实习经历"),
    ("projects", "项目经历"),
    ("skills", "技能证书"),
    ("campus", "校园经历与荣誉"),
    ("summary", "自我评价"),
]

SIDEBAR_SECTIONS: list[tuple[str, str]] = [
    ("education", "教育背景"),
    ("skills", "技能证书"),
    ("campus", "校园经历与荣誉"),
    ("experience", "实习经历"),
    ("projects", "项目经历"),
    ("summary", "自我评价"),
]
SIDEBAR_KEYS = ["education", "skills", "campus"]

# 双栏模板把「技能证书」放在第一页第三块，其余模板在项目之后
TWO_COLUMN_SECTIONS: list[tuple[str, str]] = [
    ("education", "教育背景"),
    ("experience", "实习经历"),
    ("skills", "技能证书"),
    ("projects", "项目经历"),
    ("campus", "校园经历与荣誉"),
    ("summary", "自我评价"),
]

# 从 skill库 的 18 套模板提取：模板编号 -> (body 变体类, 布局, 是否两页)
_TEMPLATES: list[tuple[str, str, str, bool, str]] = [
    ("01", "variant-blue-line", "flow", False, "蓝色分隔线"),
    ("02", "variant-band", "flow", False, "色块标题"),
    ("03", "variant-english wide-header variant-timeline", "flow", False, "英文栏目 + 时间轴"),
    ("04", "variant-top-banner", "flow", False, "顶部色块横幅"),
    ("05", "variant-gray variant-sidebar", "sidebar", False, "灰色 + 左侧栏"),
    ("06", "variant-black variant-timeline variant-two-page", "flow", True, "黑色时间轴，两页"),
    ("07", "variant-right-rail", "flow", False, "右侧色条"),
    ("08", "variant-band variant-two-column variant-two-page", "two-column", True, "色块双栏，两页"),
    ("09", "variant-black variant-cards variant-two-page", "flow", True, "黑色卡片，两页"),
    ("10", "variant-ribbon", "flow", False, "缎带标题"),
    ("11", "variant-gray variant-serif variant-split", "flow", False, "灰色衬线"),
    ("12", "variant-centered variant-compact variant-portfolio variant-two-page", "flow", True, "居中作品集，两页"),
    ("13", "variant-black variant-compact variant-sidebar", "sidebar", False, "黑色紧凑侧栏"),
    ("14", "variant-blue-line variant-compact variant-two-column variant-two-page", "two-column", True, "蓝线双栏，两页"),
    ("15", "variant-band variant-modern", "flow", False, "色块现代"),
    ("16", "variant-dense variant-two-column variant-two-page", "two-column", True, "高密度双栏，两页"),
    ("17", "variant-black variant-compact variant-two-page variant-academic", "flow", True, "黑色学术，两页"),
    ("18", "variant-blue-line variant-compact variant-two-page variant-modern", "flow", True, "蓝线紧凑现代，两页"),
]


def _build() -> dict[str, TemplateMeta]:
    out: dict[str, TemplateMeta] = {}
    for key, classes, layout, two_page, desc in _TEMPLATES:
        out[f"tpl-{key}"] = TemplateMeta(
            key=f"tpl-{key}",
            name=f"{key} · {desc}",
            body_classes=classes.split(),
            layout=layout,
            sections=(
                SIDEBAR_SECTIONS
                if layout == "sidebar"
                else TWO_COLUMN_SECTIONS
                if layout == "two-column"
                else DEFAULT_SECTIONS
            ),
            sidebar_keys=SIDEBAR_KEYS if layout == "sidebar" else [],
            two_page=two_page,
            description=desc,
        )
    # ASu 单栏：独立样式表，不走 18 套的变体类
    out["asu"] = TemplateMeta(
        key="asu",
        name="ASu 单栏高密度（默认）",
        body_classes=["asu"],
        layout="asu",
        sections=[
            ("education", "教育经历"),
            ("experience", "实习经历"),
            ("projects", "项目经历"),
            ("campus", "校园经历"),
            ("skills", "技能与证书"),
            ("summary", "自我评价"),
        ],
        description="单栏 A4 分页，信息密度最高",
    )
    return out


TEMPLATES = _build()
DEFAULT_TEMPLATE = "asu"


def get_template(key: str) -> TemplateMeta:
    try:
        return TEMPLATES[key]
    except KeyError as exc:
        raise ValueError(f"未知模板: {key}") from exc


def list_templates() -> list[dict]:
    return [
        {
            "key": t.key,
            "name": t.name,
            "description": t.description,
            "layout": t.layout,
            "body_classes": t.body_classes,
            "two_page": t.two_page,
        }
        for t in TEMPLATES.values()
    ]


def load_frame_css() -> str:
    """18 套模板共用样式：base.css（含 toolbar 的 @import 展开）。"""
    base = (FRAME_DIR / "base.css").read_text(encoding="utf-8")
    toolbar = (FRAME_DIR / "toolbar.css").read_text(encoding="utf-8")
    css = base.replace('@import url("toolbar.css");', toolbar)
    # 壳文件预览专用规则块，交付产物必须移除
    import re

    return re.sub(
        r"\n/\* @@SHELL-ONLY-START@@[\s\S]*?@@SHELL-ONLY-END@@ \*/\n", "\n", css
    )


def load_asu_css() -> str:
    return (ASU_DIR / "base.css").read_text(encoding="utf-8")


def template_catalog_json() -> str:
    return json.dumps(list_templates(), ensure_ascii=False, indent=2)
