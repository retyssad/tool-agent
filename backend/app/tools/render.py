"""resume.json → 自包含 HTML。

两套渲染路径：
- `asu`：ASu 单栏样式（assets/frame/asu/base.css）
- `tpl-*`：18 套模板共用样式（assets/frame/base.css），差异来自 body 变体类与布局

渲染是确定性的：同一份 resume.json + 同一模板，产物逐字节稳定。
"""
from __future__ import annotations

from html import escape
from typing import Any

from app.tools.templates import TemplateMeta, get_template, load_asu_css, load_frame_css

SECTION_TITLES = {
    "education": "教育背景",
    "experience": "实习经历",
    "projects": "项目经历",
    "skills": "技能证书",
    "campus": "校园经历与荣誉",
    "summary": "自我评价",
}


def render(resume: dict[str, Any], template_key: str) -> str:
    meta = get_template(template_key)
    profile = resume.get("profile") or {}
    title = f"{profile.get('name', '简历')}｜{profile.get('headline', '')}".rstrip("｜")
    if meta.layout == "asu":
        body = _render_asu(resume, meta)
        css = load_asu_css()
        body_attr = 'data-page-mode="paged"'
    else:
        body = _render_template(resume, meta)
        css = load_frame_css()
        body_attr = ""
    body_class = " ".join(meta.body_classes)
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>\n{css}\n/* 导出微调：去掉屏幕端工具栏与阴影 */\n"
        ".resume-toolbar{display:none!important}\n"
        "@media print{.resume-page{min-height:auto!important;"
        "box-shadow:none!important;margin:0!important}}\n"
        f"</style>\n</head>\n"
        f'<body class="{escape(body_class)}" {body_attr}>\n{body}\n</body>\n</html>\n'
    )


# ---------- 通用片段 ----------

def _contact_items(profile: dict[str, Any]) -> list[str]:
    items = []
    if profile.get("location"):
        items.append(escape(profile["location"]))
    if profile.get("phone"):
        items.append(f"电话：{escape(profile['phone'])}")
    if profile.get("email"):
        items.append(f"邮箱：{escape(profile['email'])}")
    if profile.get("wechat"):
        items.append(f"微信：{escape(profile['wechat'])}")
    if profile.get("english_level"):
        items.append(f"英语：{escape(profile['english_level'])}")
    if profile.get("github"):
        items.append(escape(profile["github"]))
    if profile.get("political_status"):
        items.append(escape(profile["political_status"]))
    if profile.get("birth_year"):
        items.append(f"出生年月：{escape(profile['birth_year'])}")
    return items


def _content_weight(resume: dict[str, Any]) -> int:
    """粗估内容量：条目数 + bullet 数，用于决定两页模板是否真的分页。"""
    weight = 0
    for section in ("education", "experience", "projects", "campus"):
        for entry in resume.get(section) or []:
            weight += 1 + len(entry.get("bullets") or [])
    for section in ("skills", "summary"):
        weight += len(resume.get(section) or [])
    return weight


def _label_html(label: str) -> str:
    """标签统一带全角冒号；模型输出的 label 可能已带冒号，避免重复。"""
    label = (label or "").strip().rstrip(":：")
    return f"<strong>{escape(label)}：</strong>" if label else ""


def _bullets(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    rows = []
    for item in items:
        label = _label_html(item.get("label") or "")
        text = escape(item.get("text") or "")
        rows.append(f"<li>{label}{text}</li>")
    return '<ul class="bullet-list">' + "".join(rows) + "</ul>"


def _skill_lines(entries: list[dict[str, Any]]) -> str:
    rows = []
    for item in entries:
        label = _label_html(item.get("label") or "")
        text = escape(item.get("text") or "")
        rows.append(f"<li>{label}{text}</li>")
    return '<ul class="bullet-list standalone">' + "".join(rows) + "</ul>" if rows else ""


def _paragraphs(entries: list[dict[str, Any]]) -> str:
    parts = []
    for item in entries:
        label = item.get("label") or ""
        text = item.get("text") or ""
        parts.append(
            f'<p class="section-copy">{_label_html(label)}{escape(text)}</p>'
        )
    return "".join(parts)


# ---------- 18 套模板 ----------

def _entry_block(section: str, entry: dict[str, Any]) -> str:
    if section == "education":
        head = (
            '<div class="entry-head">'
            f'<span class="date">{escape(entry.get("dates", ""))}</span>'
            f'<strong class="org">{escape(entry.get("school", ""))}</strong>'
            f'<strong class="role">{escape(entry.get("major_degree", ""))}</strong>'
            "</div>"
        )
    else:
        org = entry.get("organization") or entry.get("name") or ""
        role = entry.get("role") or entry.get("summary") or ""
        dates = entry.get("dates") or ""
        head = (
            '<div class="entry-head">'
            f'<span class="date">{escape(dates)}</span>'
            f'<strong class="org">{escape(org)}</strong>'
            f'<strong class="role">{escape(role)}</strong>'
            "</div>"
        )
    return f'<article class="entry">{head}{_bullets(entry.get("bullets") or [])}</article>'


def _section_block(section: str, entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    title = SECTION_TITLES[section]
    if section == "skills":
        inner = _skill_lines(entries)
    elif section == "summary":
        inner = _paragraphs(entries)
    else:
        inner = "".join(_entry_block(section, e) for e in entries)
    return (
        '<section class="resume-section">'
        f"<h2 class=\"section-title\"><span>{escape(title)}</span></h2>{inner}</section>"
    )


def _render_template(resume: dict[str, Any], meta: TemplateMeta) -> str:
    profile = resume.get("profile") or {}
    contacts = "".join(f"<span>{c}</span>" for c in _contact_items(profile))
    photo = ""
    if profile.get("photo_data_uri"):
        photo = (
            '<figure class="photo-frame has-photo">'
            f'<img src="{escape(profile["photo_data_uri"])}" alt="证件照"></figure>'
        )

    header = (
        '<header class="resume-header">'
        '<div class="identity">'
        f"<h1>{escape(profile.get('name', ''))}</h1>"
        f'<p class="headline">{escape(profile.get("headline", ""))}</p>'
        f'<div class="contact-grid">{contacts}</div>'
        "</div>"
        f"{photo}"
        "</header>"
    )

    section_html = {
        key: _section_block(key, resume.get(key) or []) for key, _ in meta.sections
    }
    order = [key for key, _ in meta.sections]

    if meta.layout == "sidebar":
        side = "".join(section_html[k] for k in meta.sidebar_keys if k in order)
        main = "".join(section_html[k] for k in order if k not in meta.sidebar_keys)
        body = (
            f'<div class="layout-sidebar"><aside class="sidebar">{side}</aside>'
            f'<div class="main-column">{main}</div></div>'
        )
    elif meta.layout == "two-column":
        # 不强制分页：内容短时一页放完，内容长时由打印引擎自然分页。
        # 章节设 break-inside: avoid，避免标题与首条内容被切断。
        keys = [k for k in order if section_html.get(k)]
        body = '<div class="layout-two-column">' + "".join(
            section_html[k] for k in keys
        ) + "</div>"
    else:
        body = "".join(section_html[k] for k in order)

    return f'<main class="resume-page">{header}{body}</main>'


# ---------- ASu ----------

def _asu_company(entry: dict[str, Any], section: str) -> str:
    if section == "education":
        name = (
            f'<div class="company-name education-name">{escape(entry.get("school", ""))}'
            f'<span class="education-major">· {escape(entry.get("major_degree", ""))}</span></div>'
        )
    else:
        org = entry.get("organization") or entry.get("name") or ""
        role = entry.get("role") or ""
        name = f'<div class="company-name">{escape(org)}' + (
            f" · {escape(role)}" if role else ""
        ) + "</div>"
    return (
        '<div class="company">'
        f"{name}"
        f'<div class="company-meta">{escape(entry.get("dates", ""))}</div>'
        "</div>"
    )


def _asu_section(section: str, entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    title = SECTION_TITLES[section]
    if section == "skills":
        inner = '<div contenteditable="true">' + "".join(
            f"{_label_html(e.get('label', ''))}{escape(e.get('text', ''))}<br>"
            for e in entries
        ) + "</div>"
    elif section == "summary":
        inner = "".join(
            f'<p class="section-copy">{_label_html(e.get("label", ""))}{escape(e.get("text", ""))}</p>'
            for e in entries
        )
    elif section == "education":
        inner = "".join(_asu_company(e, section) for e in entries)
    else:
        parts = []
        for entry in entries:
            parts.append(_asu_company(entry, section))
            summary = entry.get("summary")
            bullets = entry.get("bullets") or []
            if summary or bullets:
                parts.append('<article class="project">')
                if summary:
                    parts.append(f'<div class="project-title">{escape(summary)}</div>')
                if bullets:
                    rows = []
                    for b in bullets:
                        rows.append(
                            f"<li>{_label_html(b.get('label') or '')}{escape(b.get('text') or '')}</li>"
                        )
                    parts.append("<ul>" + "".join(rows) + "</ul>")
                parts.append("</article>")
        inner = "".join(parts)
    return f'<section class="section"><h2 class="section-title">{escape(title)}</h2>{inner}</section>'


def _render_asu(resume: dict[str, Any], meta: TemplateMeta) -> str:
    profile = resume.get("profile") or {}
    contacts = _contact_items(profile)
    meta_line1 = "".join(
        f"<span>{c}</span>" for c in contacts[:3]
    )
    meta_line2 = "".join(f"<span>{c}</span>" for c in contacts[3:])
    header = (
        '<header class="header-grid"><div>'
        f"<h1>{escape(profile.get('name', ''))}</h1>"
        f'<div class="meta-row">{meta_line1}</div>'
        + (f'<div class="meta-row">{meta_line2}</div>' if meta_line2 else "")
        + f'<div class="meta-row"><strong>{escape(profile.get("headline", ""))}</strong></div>'
        "</div></header>"
    )
    body = "".join(_asu_section(k, resume.get(k) or []) for k, _ in meta.sections)
    return f'<main class="sheet">{header}{body}</main>'
