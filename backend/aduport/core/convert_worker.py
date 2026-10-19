"""Isolated convert worker — run via ``python -m aduport.core.convert_worker``.

Keeps native crashes (docling / WeasyPrint / etc.) out of the API process.
Side effects (warnings / metrics / audit) are returned in status.json for the
parent to merge (report P1-7 / P2-11).
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
        from aduport.core.converter import convert
        from aduport.core.exceptions import AduPortError
        from aduport.core.isolate_effects import (
            capture_worker_effects_finish,
            capture_worker_effects_start,
        )

        state = capture_worker_effects_start()
        try:
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
                docx_theme=req.get("docx_theme"),
                rag_max_chars=req.get("rag_max_chars"),
                rag_chunk_overlap=req.get("rag_chunk_overlap"),
                embed=req.get("embed"),
                embed_backend=req.get("embed_backend"),
                embed_model=req.get("embed_model"),
                vector_persist=req.get("vector_persist"),
                vector_collection=req.get("vector_collection"),
                pdf_engine=req.get("pdf_engine"),
                ocr_engine=req.get("ocr_engine"),
                force_ocr=req.get("force_ocr"),
                ocr_max_pages=req.get("ocr_max_pages"),
                skip_pdf_sidecar=req.get("skip_pdf_sidecar"),
                cache_bypass=req.get("cache_bypass"),
                docx_auto_toc=req.get("docx_auto_toc"),
                latex_plain_comments=req.get("latex_plain_comments"),
                csv_table_only=req.get("csv_table_only"),
                fetch_images=req.get("fetch_images"),
                skip_magic=bool(req.get("skip_magic", False)),
            )
            out_path.write_bytes(out)
            status = {"ok": True, "bytes_out": len(out)}
            status.update(capture_worker_effects_finish(state))
            write_status(status)
            return 0
        except Exception as exc:  # noqa: BLE001 — must report any failure to parent
            status: dict = {}
            status.update(capture_worker_effects_finish(state))
            if isinstance(exc, AduPortError):
                status.update({"ok": False, "code": exc.code, "message": exc.message})
                write_status(status)
                return 2
            status.update(
                {
                    "ok": False,
                    "code": "conversion_error",
                    "message": str(exc) or "conversion failed",
                }
            )
            write_status(status)
            return 1
    except Exception as exc:  # noqa: BLE001
        try:
            from aduport.core.exceptions import AduPortError

            if isinstance(exc, AduPortError):
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
