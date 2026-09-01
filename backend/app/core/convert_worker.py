"""Isolated convert worker — run via ``python -m app.core.convert_worker``.

Keeps native crashes (docling / WeasyPrint / etc.) out of the API process.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print("usage: convert_worker <req.json> <out.bin> <status.json>", file=sys.stderr)
        return 64

    req_path = Path(args[0])
    out_path = Path(args[1])
    status_path = Path(args[2])

    def write_status(payload: dict) -> None:
        status_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    try:
        req = json.loads(req_path.read_text(encoding="utf-8"))
        content = Path(req["input_path"]).read_bytes()
        from app.core.converter import convert
        from app.core.exceptions import MdBridgeError

        out = convert(
            content,
            req["source_format"],
            req["target_format"],
            filename=req.get("filename") or "",
            client=req.get("client") or "",
            pdf_font_family=req.get("pdf_font_family"),
            pdf_custom_css=req.get("pdf_custom_css"),
            pdf_page_size=req.get("pdf_page_size"),
            pdf_orientation=req.get("pdf_orientation"),
            pdf_header=req.get("pdf_header"),
            pdf_footer=req.get("pdf_footer"),
            pdf_page_numbers=req.get("pdf_page_numbers"),
            pdf_toc=req.get("pdf_toc"),
            fetch_images=req.get("fetch_images"),
            skip_magic=bool(req.get("skip_magic", False)),
        )
        out_path.write_bytes(out)
        write_status({"ok": True, "bytes_out": len(out)})
        return 0
    except Exception as exc:  # noqa: BLE001 — must report any failure to parent
        try:
            from app.core.exceptions import MdBridgeError

            if isinstance(exc, MdBridgeError):
                write_status({"ok": False, "code": exc.code, "message": exc.message})
                return 2
        except Exception:
            pass
        write_status(
            {
                "ok": False,
                "code": "conversion_error",
                "message": str(exc) or "conversion failed",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
