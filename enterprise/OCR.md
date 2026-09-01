# Optional OCR stack (enterprise GPU / Apple Silicon)

## Install

```bash
# MinerU (balanced default) — pins transformers<5 via extras
pip install 'md-bridge[ocr-mineru]'

# PaddleOCR-VL (accurate / fallback) — needs PaddlePaddle + paddlex[ocr]
pip install 'md-bridge[ocr-paddle]'
# or both:
pip install 'md-bridge[ocr]'
```

First run downloads models (ModelScope / PaddleX). Prefer a writable cache home:

```bash
export MDBRIDGE_OCR_HOME=/data/ocr_home   # or project .paddle_home
# equivalently set HOME to that path before starting the API
```

## Enable

```bash
MDBRIDGE_OCR_ENABLED=true
MDBRIDGE_OCR_ENGINE=auto
# balanced = MinerU → PaddleOCR-VL → docling
# accurate = PaddleOCR-VL → MinerU → docling
MDBRIDGE_OCR_MODE=balanced
MDBRIDGE_OCR_FALLBACK=paddleocr_vl
MDBRIDGE_OCR_MIN_CHARS=40
MDBRIDGE_OCR_TIMEOUT_SECONDS=300
MDBRIDGE_MINERU_BIN=mineru
MDBRIDGE_MINERU_BACKEND=pipeline
# Formula OCR is heavy (Unimernet); leave off unless needed
MDBRIDGE_MINERU_FORMULA=false
MDBRIDGE_MINERU_TABLE=true
MDBRIDGE_OCR_HOME=/data/ocr_home
```

Force a single engine:

```bash
# MDBRIDGE_PDF_ENGINE=mineru
# MDBRIDGE_PDF_ENGINE=paddleocr_vl
```

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
