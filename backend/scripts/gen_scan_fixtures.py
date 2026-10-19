#!/usr/bin/env python3
"""Generate image-only (scan-like) PDF fixtures for OCR regression.

Usage (from backend venv):
  python scripts/gen_scan_fixtures.py
"""

from __future__ import annotations

import random
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "scan"

PAGES = [
    {
        "name": "scan_en_cjk_mixed",
        "lines": [
            "AduPort Scan Regression Fixture",
            "English line: Hello OCR Sample 2026",
            "中文行：扫描件识别验收样张",
            "混排：合同编号 CONTRACT-OCR-001",
            "表格暗示：姓名 | 金额 | 日期",
            "Alice | 1200.50 | 2026-09-01",
        ],
        "must_contain_any": ["Hello OCR", "扫描件", "CONTRACT-OCR", "Alice"],
    },
    {
        "name": "scan_cjk_dense",
        "lines": [
            "第一章 总则",
            "为保障文档转换质量，本样张用于扫描 OCR 回归。",
            "条款一：甲方应在五个工作日内完成验收。",
            "条款二：乙方交付离线镜像与验收报告。",
            "关键词：私有化部署、一机一码、双引擎OCR",
        ],
        "must_contain_any": ["总则", "验收", "私有化", "一机一码", "双引擎"],
    },
]


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_page(lines: list[str], size: tuple[int, int] = (1240, 1754)) -> Image.Image:
    img = Image.new("RGB", size, (252, 252, 248))
    draw = ImageDraw.Draw(img)
    rnd = random.Random(42)
    px = img.load()
    for _ in range(8000):
        x, y = rnd.randint(0, size[0] - 1), rnd.randint(0, size[1] - 1)
        v = rnd.randint(230, 255)
        px[x, y] = (v, v, v - 2)
    title_font = _font(42)
    body_font = _font(34)
    y = 80
    for i, line in enumerate(lines):
        f = title_font if i == 0 else body_font
        draw.text((72, y), line, fill=(20, 20, 20), font=f)
        y += 70 if i == 0 else 56
    draw.rectangle([40, 40, size[0] - 40, size[1] - 40], outline=(200, 200, 200), width=2)
    return img


def image_to_pdf_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PDF", resolution=150.0)
    return buf.getvalue()


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for page in PAGES:
        img = render_page(page["lines"])
        pdf = image_to_pdf_bytes(img)
        (ROOT / f"{page['name']}.pdf").write_bytes(pdf)
        img.save(ROOT / f"{page['name']}.png")
        (ROOT / f"{page['name']}.expect.txt").write_text(
            "\n".join(page["must_contain_any"]) + "\n", encoding="utf-8"
        )
        text_layer = ""
        try:
            from pypdf import PdfReader

            text_layer = "".join(
                (p.extract_text() or "") for p in PdfReader(BytesIO(pdf)).pages
            ).strip()
        except Exception:
            pass
        print(f"{page['name']}: {len(pdf)} bytes, text_layer_len={len(text_layer)}")

    (ROOT / "README.md").write_text(
        "# Scan OCR fixtures\n\n"
        "Image-only PDFs (Pillow raster embed) for dual-engine regression.\n"
        "Each `*.expect.txt` lists substrings; OCR output should match **any** line.\n"
        "Regenerate: `python scripts/gen_scan_fixtures.py`\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
