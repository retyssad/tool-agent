"""PDF 抽取测试：用 pypdf 现场生成，不依赖外部文件。"""
from __future__ import annotations

import io

import pypdf
import pytest

from app.tools.pdf import MIN_USABLE_CHARS, extract_pdf


def _pdf_with_text(text: str) -> bytes:
    """构造一页极简 PDF：内容流用 BT/Tj 写入 ASCII（中文需嵌入字体，不在此测）。"""
    content = f"BT /F1 12 Tf 50 780 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def test_extract_blank_pdf_has_no_text_layer():
    result = extract_pdf(_pdf_with_text(""))
    assert result.page_count == 1
    assert result.has_text_layer is False
    assert result.is_probably_scanned is True


def test_extract_ascii_pdf_reads_text():
    payload = "A" * (MIN_USABLE_CHARS + 10)
    result = extract_pdf(_pdf_with_text(payload))
    assert result.has_text_layer is True
    assert payload[:20] in result.text


def test_extract_rejects_encrypted():
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("secret")
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(ValueError, match="加密"):
        extract_pdf(buf.getvalue())


def test_extract_rejects_non_pdf():
    with pytest.raises(Exception):
        extract_pdf(b"this is not a pdf")
