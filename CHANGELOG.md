# Changelog

## [0.16.0] - 2026-09-01

anydoc 源码级融入 + 自有 PDF 文本层依赖（相对 0.11 线累计至当前包版本）。

### Added
- **Vendored anydoc (Firecrawl) as `mdbridge_native._doc`**: Rust sources under `native/anydoc/`; maturin builds an in-process abi3 extension (**not** shipped on PyPI — use Docker `INSTALL_NATIVE=1` or `scripts/build_native.sh`)
- **Importer bridge**: fast path (`to_markdown_bytes`) and model path (`to_document` → `DocumentModel`); hosted OCR removed
- **Ops**: `MDBRIDGE_ANYDOC_*` (incl. `FALLBACK` / `SNIFF`); `/health` `anydoc_available` / `anydoc_version`; `/formats` only/led lists
- **Docker multi-stage**: install only `mdbridge_native*.whl` when `INSTALL_NATIVE=1`
- **Scripts**: `build_native.sh`, `sync_anydoc.sh` (pinned via `UPSTREAM_PIN`), `docker_build_safe.sh` (ASCII-safe context)
- **Core PDF text-layer**: `pypdfium2>=4.30` first-class (fixes digital PDF 0-char in slim/dev)
- **Telemetry**: `mdbridge_anydoc_hit_rate`, `mdbridge_anydoc_channel_share`, `mdbridge_anydoc_hits_by_channel{channel=}`
- **DOCX product switches**: Form `docx_theme`; `MDBRIDGE_DOCX_EXPORT_COMMENTS` / `MDBRIDGE_HEADING_NUMBERING`; `/health` exposes theme/comments/numbering
- **XLSX value export**: `Table.cells` fallback; import metadata `export_policy=values_only`
- **OCR readiness**: `/health` `ocr_ready` + `ocr.install_hint` when engines missing
- **Product surface**: Web PDF/DOCX advanced options; job `callback_url` webhook; `MDBRIDGE_JOB_RESULT_DIR` result blobs; CLI PDF/DOCX parity; `docs/document-model.md` + `ENTERPRISE.md`
- **Vertical depth (v1.5)**: RTF export target; PDF two-column heuristic (`MDBRIDGE_PDF_TEXT_MULTICOLUMN`); xlsx sheet names + header bold styles; PPTX multi-textbox layout; tenant `daily_quota` + `metrics.alerts`; Docker `INSTALL_OCR=1` build-arg
- **Ops & quality (v1.6)**: `docs/ops/prometheus/` Grafana dashboard + alert rules; metrics `mdbridge_ocr_ready`, `mdbridge_conversions_failed_by_code`; golden matrix CI `test_golden_matrix.py`
- **P2-4 breadth**: default `MDBRIDGE_ANYDOC_FORMATS` adds **epub** + **csv** (anydoc-first, native fallback); fixture tests in `test_anydoc_bridge.py`
- **Comment export**: `html_render` renders `<aside class="comment">` for HTML/EPUB/MOBI/AZW3 (`test_html_comment_export.py`)
- **§2 vertical (v1.8)**: enterprise `INSTALL_OCR=1` + `MDBRIDGE_OCR_ENABLED` default; PDF/PPTX Comment; Prometheus templates
- **AST depth (v1.9)**: `Revision`; DOCX `section_props`; `Image.wrap`; xlsx formulas + pivot metadata; PDF layout meta; PPTX layouts; EPUB DRM detect (`test_accepted_depth.py`)
- **Registry fix**: restore full exporter/importer registration (xlsx/odt/csv/json/latex/rtf + anydoc wiring)

### Changed
- Default `MDBRIDGE_ANYDOC_FORMATS` includes **doc** + **pptx** + **epub** + **csv** (Rust fast→md); **xlsx** stays native (header-row); **pdf** stays md-bridge engines
- Frontend source formats expose ppt / xls / ods / odp / xlsb siblings
- `/formats` notes document docx / xlsx / ocr / jobs contracts
- `SUPPORTED_TARGET_FORMATS` includes **rtf**; Web target dropdown aligned

### Notes
- PyPI/SDK: Python package only — compile native separately for high-fidelity Office import
- Colima: prefer `scripts/docker_build_safe.sh` when the checkout path is non-ASCII

## [0.11.0] - 2026-08-31


### Added
- **DOCX OOXML enrich**: import post-pass for `comments.xml` / headers / theme → AST + metadata
- **MD autolink** bare `http(s)://` URLs; **footnote cross-refs** `[^id]` ↔ `#fn-id` in HTML
- **HTML KaTeX** CDN auto-render for MathBlock (`MDBRIDGE_HTML_KATEX`)
- **MOBI text fallback**: PalmDB extract when docling missing/fails
- **Table header switch**: `MDBRIDGE_TABLE_FIRST_ROW_HEADER` (default true)
- CI: ruff hard-fail, coverage ≥70%, pip-audit (advisory)

### Changed
- Version 0.11.0

## [0.10.0] - 2026-08-31

### Added
- **KF8 / AZW3** export with EXTH + embedded image records (`recindex`)
- **True Word comments**: `word/comments.xml` + commentRange markers (Review pane)
- **边解析边转**: chunked MD/TXT/HTML convert with spill (`MDBRIDGE_STREAM_CONVERT`)

### Changed
- Version 0.10.0

## [0.9.0] - 2026-08-31

### Added
- **MOBI export** (minimal PalmDOC/MOBI, UTF-8 HTML payload)
- **PDF text-layer parallel extract** (`MDBRIDGE_PDF_TEXT_PARALLEL` / `PDF_TEXT_WORKERS`)
- **Gzip result cache** (`MDBRIDGE_CACHE_GZIP`, min size threshold)
- **Startup warmup** for docling converters + WeasyPrint (`MDBRIDGE_WARMUP_ON_STARTUP`)
- **HTML importer harden**: nested div/section, blockquote/hr, script skip, stack pop-to-match

### Changed
- Version 0.9.0 — targets now 8×8 (full matrix for registered formats)

## [0.8.0] - 2026-08-31

### Added
- **PPTX export** via python-pptx (title + content slides)
- **Streaming upload**: chunked `UploadFile.read` → SpillBuffer (`read_upload_streaming`)
- **DOCX themes** (`MDBRIDGE_DOCX_THEME=default|academic|minimal`) + **Comment** AST → 批注 section
- **ListItem.children** for multi-paragraph list items
- Cache hit can skip magic/virus re-check (`MDBRIDGE_CACHE_SKIP_REVALIDATE`)
- Rate-limit IP key pruning; audit detail path sanitization
- GitHub Actions CI (pytest + frontend build)

### Changed
- Version bump to 0.8.0; targets now include pptx (8×7)

## [0.7.0] - 2026-08-31

### Added
- **Target formats**: `html` / `txt` / `epub` exporters (8×6 matrix vs prior 8×3)
- **PDF layout**: page size, orientation, header/footer, page numbers, TOC (`MDBRIDGE_PDF_*` + form fields)
- **Remote images**: SSRF-safe HTTP(S) fetch into `Image.data` (`MDBRIDGE_FETCH_REMOTE_IMAGES`)
- **Disk spill**: async job payloads ≥ threshold written to 0600 temp files (`MDBRIDGE_SPILL_THRESHOLD_MB`)
- **DOCX**: section header (title) + PAGE footer; multi-level list indent

### Changed
- Version bump to 0.7.0

## [0.6.0] - 2026-08-31

### Added
- AST semantic nodes: **Blockquote**, **HorizontalRule**, **MathBlock**, **Footnote**, **HtmlBlock**; GFM task lists via `ListItem.checked`
- MD import/export round-trip for quote / HR / tasks / footnotes / `$$` math / safe HTML subset
- Process-wide **DocumentConverter singleton** (office + PDF) — avoids per-request rebuild
- API `source_format` optional / `auto`: server-side extension + magic-byte detection

### Changed
- Version bump to 0.6.0

## [0.5.1] - 2026-08-31

### Fixed
- PDF import no longer hard-fails on Hugging Face Hub timeout: `auto` engine uses **pypdfium2 text-layer** when enough embedded text exists, and falls back after Hub errors
- CJK text-layer join: no longer inserts spaces between Chinese soft-wrapped lines
- **PEP math-font mojibake**: remap textbook italic Latin (犃/犅/狓… → A/B/x…) so geometry proofs are readable
- Default `HF_ENDPOINT=https://hf-mirror.com` in `start.sh` for docling model downloads
- UI auto-selects source format from uploaded filename

### Added
- `scripts/download_docling_models.sh` — pre-download layout/table/OCR weights via HF mirror

## [0.5.0] - 2026-08-31

### Added (Ch.4 安全/功能收尾)
- **MOBI** 导入（docling `[office]`；`.mobi` / `.azw` / `.azw3`）
- **病毒扫描钩子**：`MDBRIDGE_VIRUS_SCAN` + ClamAV（`clamscan`/`clamdscan`）
- **Web**：中英切换、队列拖拽排序、服务端任务结果页（`GET /api/jobs`）
- **OCR 产品化**：`/api/health.ocr` 标注「扫描 PDF → 结构化 Markdown」卖点
- Tests: `test_v05_upgrade.py`

## [0.4.0] - 2026-08-31

### Added (Ch.4 路线图续作)
- **Async jobs:** `POST /api/jobs` → poll `GET /api/jobs/{id}` → `GET /api/jobs/{id}/result`；Web UI 进度百分比
- **Formats:** PPTX / EPUB 导入（需 `pip install 'md-bridge[office]'` / docling）
- **Security:** 可选 HTTP Basic Auth（`MDBRIDGE_BASIC_AUTH=user:pass`，可与 API-Key 二选一通过）
- **Ops:** 异步任务内存预算（`MDBRIDGE_MEMORY_BUDGET_MB`）+ 健康检查暴露 jobs 统计
- Tests: +9（`test_v04_upgrade.py`）

## [0.3.0] - 2026-08-31

### Added (Ch.4 完善·优化·升级)
- **Security:** magic-bytes + extension whitelist; per-IP rate limit; JSONL audit log; temp files `chmod 0600`
- **Formats:** TXT / HTML importers; `/api/formats` returns `source` / `target` lists
- **Ops:** SHA256 result cache (TTL + size cap); Prometheus `/api/metrics`; enhanced `/api/health`
- **Architecture:** importer/exporter plugin registry (replaces hard-coded convert branches)
- **Web UI:** conversion history (localStorage), download-all, source/target format split (TXT/HTML import)
- **PDF:** optional CJK first-line indent via `MDBRIDGE_PDF_CJK_TYPESET`
- Tests: +9 cases (`test_v03_upgrade.py`); suite **35 passed**; core-path coverage **~86%**

### Fixed
- Metrics deadlock: `snapshot()` / `prometheus_text()` no longer re-enter `threading.Lock` via `p95_latency()`

## [0.2.0] - 2026-08-31

### Fixed (P0–P3 from commercial readiness review / WBS Ch.3)
- Circular imports: package `__init__.py` no longer eagerly imports submodules
- ZIP Slip: batch output names sanitized with basename + whitelist
- Markdown importer: tables, bold/italic/link (no duplication), images, code, strikethrough, nested lists
- CORS: localhost whitelist by default; credentials off unless configured
- CSS SSRF: strip `@import` / `url()` / `@font-face`; WeasyPrint remote fetch blocked
- Auth: optional `X-API-Key`; Docker Compose binds `127.0.0.1` only
- Batch: file count / total size limits; thread-pool concurrency; error message redaction
- PDF exporter: merge adjacent list items; safer CSS
- DOCX exporter: real hyperlinks; inline code / strikethrough
- Frontend: multi-file list, concurrent convert, correct toast, delayed revokeObjectURL
- Dependencies: docling moved to optional extra `[office]`; `Dockerfile.slim` + compose overlay
- Tests: 26 pytest cases; core-path coverage 82.9%; GitHub Actions CI; TemporaryDirectory for importers

## [0.1.0] - 2026-08-31

- Initial Alpha: DocumentModel AST, CLI/API/Web UI, Docker Compose
