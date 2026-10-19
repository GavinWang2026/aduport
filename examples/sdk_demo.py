#!/usr/bin/env python3
"""AduPort Python SDK usage example — 使用公共 API 面（``from aduport import ...``）。

安装后（``pip install aduport``）同样的代码即可直接运行。
"""

from pathlib import Path

from aduport import (
    SUPPORTED_SOURCE_FORMATS,
    SUPPORTED_TARGET_FORMATS,
    capabilities,
    convert,
    convert_directory,
)

EXAMPLES_DIR = Path(__file__).parent


def demo_capabilities():
    """能力检测：分层授权（free / native / enterprise）的地基。"""
    caps = capabilities()
    print("capabilities():")
    for key in ("version", "native", "native_version", "ocr_mineru", "embed", "office_docling"):
        print(f"  {key:16} = {caps.get(key)!r}")
    print(f"  {'formats':16} = {len(SUPPORTED_SOURCE_FORMATS)} sources / "
          f"{len(SUPPORTED_TARGET_FORMATS)} targets")


def demo_docx_to_md():
    input_path = EXAMPLES_DIR / "sample.docx"
    output_path = EXAMPLES_DIR / "sample_output.md"

    if not input_path.exists():
        print(f"Skip: place a sample DOCX at {input_path}")
        return

    source_bytes = input_path.read_bytes()
    result = convert(source_bytes, "docx", "md", filename=input_path.name)
    output_path.write_bytes(result)
    print(f"Converted {input_path.name} -> {output_path.name}")


def demo_md_to_pdf():
    input_path = EXAMPLES_DIR / "sample.md"
    output_path = EXAMPLES_DIR / "sample_output.pdf"

    if not input_path.exists():
        print(f"Skip: place a sample Markdown at {input_path}")
        return

    source_bytes = input_path.read_bytes()
    result = convert(
        source_bytes,
        "md",
        "pdf",
        filename=input_path.name,
        pdf_font_family="Georgia, serif",
        pdf_custom_css="h1 { color: #1a73e8; }",
    )
    output_path.write_bytes(result)
    print(f"Converted {input_path.name} -> {output_path.name}")


def demo_batch():
    out = EXAMPLES_DIR / "_batch_out"
    result = convert_directory(EXAMPLES_DIR, out, "md", "docx")
    print(f"Batch: total={result.total} ok={result.succeeded} fail={result.failed}")


def demo_in_memory():
    md_content = b"# Hello AduPort\n\nThis is a **demo** paragraph.\n\n- item one\n- item two\n"
    docx_bytes = convert(md_content, "md", "docx")
    print(f"MD -> DOCX: {len(docx_bytes)} bytes")
    roundtrip_md = convert(docx_bytes, "docx", "md")
    print("Roundtrip preview:")
    print(roundtrip_md.decode("utf-8")[:200])


def demo_document_model():
    """First-class DocumentModel: build AST → export without going through convert().

    DocumentModel 是稳定公共导出（节点集合只增不破）；AST 节点类暂走内部模块路径。
    """
    from aduport import DocumentModel
    from aduport.core.document_model import Heading, Paragraph, Table
    from aduport.exporters.docx_exporter import export_docx
    from aduport.exporters.md_exporter import export_md

    model = DocumentModel(
        title="SDK AST demo",
        nodes=[
            Heading(level=1, text="Hand-built DocumentModel"),
            Paragraph(text="Importers and exporters share this intermediate AST."),
            Table(rows=[["Format", "Depth"], ["docx", "high"], ["xlsx", "values"]]),
        ],
        metadata={"demo": "document_model"},
    )
    md = export_md(model)
    docx = export_docx(model, theme="minimal")
    print(f"DocumentModel → MD ({len(md)} chars), DOCX ({len(docx)} bytes)")
    print(md[:160].replace("\n", "\\n"))


if __name__ == "__main__":
    print("=== AduPort SDK Demo ===\n")
    demo_capabilities()
    print()
    demo_document_model()
    print()
    demo_in_memory()
    print()
    demo_docx_to_md()
    demo_md_to_pdf()
    demo_batch()
