"""PDF 文本抽取。只用 pypdf，不引入系统依赖。"""
from __future__ import annotations

import io
from dataclasses import dataclass

import pypdf

# 少于这个字符数视为没有可用文本层（扫描件）
MIN_USABLE_CHARS = 80


@dataclass(slots=True)
class ExtractedPDF:
    text: str
    page_count: int
    has_text_layer: bool
    per_page_chars: list[int]

    @property
    def is_probably_scanned(self) -> bool:
        return not self.has_text_layer


def extract_pdf(data: bytes) -> ExtractedPDF:
    """抽取文本。加密 PDF 直接报错，不尝试破解。"""
    reader = pypdf.PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError("PDF 已加密，无法抽取文本")

    texts: list[str] = []
    counts: list[int] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - 单页失败不影响其他页
            text = ""
        texts.append(text)
        counts.append(len(text.strip()))

    merged = "\n\n".join(t.strip() for t in texts if t.strip())
    return ExtractedPDF(
        text=merged,
        page_count=len(reader.pages),
        has_text_layer=len(merged.strip()) >= MIN_USABLE_CHARS,
        per_page_chars=counts,
    )
