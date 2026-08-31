#!/usr/bin/env python3
"""md-bridge Python SDK usage example."""

from pathlib import Path

from app.core.converter import convert, convert_directory

EXAMPLES_DIR = Path(__file__).parent


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
    md_content = b"# Hello md-bridge\n\nThis is a **demo** paragraph.\n\n- item one\n- item two\n"
    docx_bytes = convert(md_content, "md", "docx")
    print(f"MD -> DOCX: {len(docx_bytes)} bytes")
    roundtrip_md = convert(docx_bytes, "docx", "md")
    print("Roundtrip preview:")
    print(roundtrip_md.decode("utf-8")[:200])


if __name__ == "__main__":
    print("=== md-bridge SDK Demo ===\n")
    demo_in_memory()
    print()
    demo_docx_to_md()
    demo_md_to_pdf()
    demo_batch()
