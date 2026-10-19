"""PDF text-layer fallback (no Hub)."""

from __future__ import annotations

from aduport.importers.pdf_font_fix import normalize_pdf_text
from aduport.importers.pdf_text_fallback import extract_pdf_text_model, pdf_text_char_count
from aduport.importers.pdf_importer import _is_hub_network_error


MIN_PDF = b"""%PDF-1.4
1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj
2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj
3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj
4 0 obj<< /Length 44 >>stream
BT /F1 24 Tf 50 80 Td (Hello PDF) Tj ET
endstream
endobj
5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000360 00000 n 
trailer<< /Size 6 /Root 1 0 R >>
startxref
433
%%EOF
"""


def test_pdf_text_fallback_extracts():
    assert pdf_text_char_count(MIN_PDF) >= 5
    model = extract_pdf_text_model(MIN_PDF, "t.pdf")
    texts = []
    for n in model.nodes:
        t = getattr(getattr(n, "text", None), "text", None) or getattr(n, "text", "")
        if hasattr(t, "text"):
            t = t.text
        texts.append(str(t))
    blob = " ".join(texts)
    assert "Hello" in blob


def test_cjk_join_no_spaces():
    from aduport.importers.pdf_text_fallback import _join_wrapped_lines, _page_to_paragraphs

    assert _join_wrapped_lines(["你好", "世界"]) == "你好世界"
    paras = _page_to_paragraphs("第一行内容\n续行内容\n\n下一段")
    assert paras[0] == "第一行内容续行内容"
    assert "下一段" in paras[1]


def test_hub_error_detection():
    assert _is_hub_network_error(Exception("ConnectTimeout: locate the files on the Hub"))
    assert not _is_hub_network_error(Exception("invalid xref table"))


def test_pep_math_font_fix():
    raw = "直线犾垂直平分线段犃犅，犘１在犾上．△犘犆犃≌△犘犆犅（ＳＡＳ）．图２０．１７"
    fixed = normalize_pdf_text(raw)
    assert "直线l垂直平分线段AB" in fixed
    assert "P1在l上" in fixed
    assert "△PCA≌△PCB(SAS)" in fixed or "△PCA≌△PCB（SAS）" in fixed or "SAS" in fixed
    assert "图20.1-7" in fixed or "图20.1-7" in fixed.replace(" ", "")
    assert "犃" not in fixed and "犾" not in fixed
