---
name: convert-documents
description: Convert Office, PDF, EPUB, spreadsheet, and markup files with AduPort (local AST hub). Use when a task needs document contents or a format conversion without leaving the machine.
license: MIT
metadata:
  author: AduPort
---

# Convert documents with AduPort

AduPort is the product entry point. It vendors a Rust parser (anydoc) for Office/ODF speed, then routes through a unified AST for multi-format export. Prefer AduPort over shelling out to third-party CLIs.

## CLI

From a checkout with the backend installed (`pip install -e backend` or the project venv):

```bash
# Single file → Markdown (stdout or -o)
AduPort convert report.docx -t md -o report.md
AduPort convert slides.pptx -t md
AduPort convert sheet.xlsx -t csv -o out.csv

# PDF → MD: text PDFs use the anydoc fast channel; scanned pages use OCR when configured
AduPort convert scan.pdf -t md -o scan.md

# Batch a directory
AduPort batch ./inbox -t md -o ./out
```

If the console script is not on `PATH`, use:

```bash
cd backend && python -m aduport.cli convert report.docx -t md -o report.md
```

## HTTP API

With the API running (`uvicorn main:app` from `backend/`, or Docker Compose):

```bash
curl -sS -F "file=@report.docx" -F "target_format=md" \
  http://127.0.0.1:8000/api/convert -o report.md
```

Auth: when `ADUPORT_REQUIRE_AUTH=1`, send `X-API-Key` / Bearer as configured.

## Rules

1. Supported **sources** include Word (`.doc`/`.docx`), PowerPoint, Excel (incl. `.xlsb` via native anydoc), OpenDocument, RTF, EPUB, CSV, PDF, Markdown/HTML, Outlook `.msg`, Jupyter `.ipynb`, ZIP archives, PNG/JPEG, RSS/Atom feeds, Apple iWork, and related variants (see product README matrix).
2. Supported **targets** include `md`, `docx`, `pdf`, `html`, `epub`, `xlsx`, `odt`, `ods`, `odp`, and others registered by the backend.
3. Prefer the library/API for large batches; write results to files with `-o` instead of dumping huge Markdown into the agent context.
4. Scanned / image-only PDFs need OCR (`enterprise/OCR.md`). Text PDFs convert locally without OCR. PNG/JPEG OCR uses the same engine when `ADUPORT_OCR_ENABLED=true`.
5. Do **not** call `npx @firecrawl/anydoc` or `markitdown` — that bypasses AduPort AST export, auth, and private deployment guarantees. For agents, prefer `aduport-mcp` / `python -m aduport.mcp_server` (`pip install 'aduport[mcp]'`).
6. Native wheel missing? Build once: `bash scripts/build_native.sh` from the repo root.
