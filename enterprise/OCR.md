# Optional OCR stack (enterprise GPU / Apple Silicon)

## Install

```bash
# MinerU (balanced default) — pins transformers<5 via extras
pip install 'aduport[ocr-mineru]'

# PaddleOCR-VL (accurate / fallback) — needs PaddlePaddle + paddlex[ocr]
pip install 'aduport[ocr-paddle]'
# or both:
pip install 'aduport[ocr]'
```

First run downloads models (ModelScope / PaddleX). Prefer a writable cache home:

```bash
export ADUPORT_OCR_HOME=/data/ocr_home   # or project .paddle_home
# equivalently set HOME to that path before starting the API
```

## Enable

```bash
ADUPORT_OCR_ENABLED=true
ADUPORT_OCR_ENGINE=auto
# balanced = MinerU → PaddleOCR-VL → docling
# accurate = PaddleOCR-VL → MinerU → docling
ADUPORT_OCR_MODE=balanced
ADUPORT_OCR_FALLBACK=paddleocr_vl
ADUPORT_OCR_MIN_CHARS=40
ADUPORT_OCR_TIMEOUT_SECONDS=300
ADUPORT_MINERU_BIN=mineru
ADUPORT_MINERU_BACKEND=pipeline
# Formula OCR is heavy (Unimernet); leave off unless needed
ADUPORT_MINERU_FORMULA=false
ADUPORT_MINERU_TABLE=true
ADUPORT_OCR_HOME=/data/ocr_home
# Cap pages sent to OCR (0 = unlimited). Full textbooks otherwise hang.
ADUPORT_OCR_MAX_PAGES=20
# Paddle on macOS: auto=off (SIGTRAP in short workers); true|false to force
ADUPORT_OCR_ALLOW_PADDLE=auto
```

## Layout sidecar (2026-09-04)

OCR engines still produce Markdown → AST, but when MinerU middle JSON / Paddle bbox
payloads are present they are normalized into `metadata.ocr_layout.blocks` and
best-effort attached as `metadata.node_spans` (page / bbox). RAG export
(`target_format=rag`, schema_version 1.1) then surfaces `page_number` / `bbox` /
`element_id`. Optional built-in embedding / local vector store: see [RAG.md](./RAG.md).

Force a single engine (process-wide):

```bash
# ADUPORT_PDF_ENGINE=mineru
# ADUPORT_PDF_ENGINE=paddleocr_vl
```

## Re-OCR closed loop (2026-09-04)

Per-request overrides (Form / job opts), independent of process env:

| Form | Effect |
|------|--------|
| `force_ocr=true` | Bypass text-layer gate; skip AST sidecar; run OCR chain |
| `pdf_engine=ocr\|mineru\|…` | Request-scoped engine |
| `ocr_engine=mineru\|paddleocr_vl` | Prefer named OCR engine |
| `ocr_max_pages=N` | Cap pages for this request |
| `skip_pdf_sidecar=true` | Ignore embedded AST sidecar |
| `cache_bypass=true` | Do not read/write result cache |

Job-level re-run (input retained when `ADUPORT_JOB_RETAIN_INPUT_ON_DONE=true`, default):

```http
POST /api/jobs/{id}/reprocess
Form: force_ocr=true&pdf_engine=mineru&cache_bypass=true
```

Layout human correction (no engine retrain) → patch RAG metadata:

```http
POST /api/rag/layout_feedback
{"payload": {…rag…}, "feedback": [{"element_id":"…","page":2,"bbox":[x0,y0,x1,y1]}]}
```

`/api/health/detail` → `ocr.reocr` flags the closed-loop surface.

## Smoke

```bash
# MinerU CLI
mineru -p sample.pdf -o /tmp/out -b pipeline -f false

# Adapter + digital PDF smoke (skips if engines missing)
cd backend && pytest -q -m integration tests/test_ocr_integration.py

# Real scan-like fixtures (image-only PDF, no text layer)
python scripts/gen_scan_fixtures.py
pytest -q -m integration tests/test_scan_ocr_regression.py
```

Fixtures live in `backend/tests/fixtures/scan/` (`scan_en_cjk_mixed`, `scan_cjk_dense`).
Each `*.expect.txt` lists tokens; OCR must hit **any** one (fuzzy gate).

## Notes

- MinerU pipeline requires `transformers>=4.57.3,<5` (5.x breaks Unimernet imports).
- Prefer CLI path for MinerU under API workers; ProcessPool can fail in odd entrypoints.
- Set `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True` (auto-set by adapter) to skip hoster probe when offline/cached.
- Formal OCR runs need a non-sandbox host (`os.sysconf(SC_SEM_NSEMS_MAX)` / ProcessPool).
