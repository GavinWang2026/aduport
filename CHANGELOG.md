# Changelog

## [Unreleased]

### Changed
- **Product rename: md-bridge → AduPort**: display name `AduPort`; PyPI/CLI `aduport` / `aduport-mcp`; env prefix `ADUPORT_*` (legacy `MDBRIDGE_*` still accepted from process env and `.env` when the new key is unset); Prometheus metrics `aduport_*`; native wheel/import `aduport-native` / `aduport_native` (falls back to pre-rename `mdbridge_native` wheels); HTTP headers `X-AduPort-*`; localStorage keys `aduport-*`; Docker image/container names `aduport-backend` / `aduport-frontend` (lowercase); `MdBridgeError` kept as an alias of `AduPortError`

### Added
- **`csv_table_only` on HTTP / CLI / jobs**: Form `csv_table_only=true`, CLI `--csv-table-only`; unknown convert Form fields return 422 instead of silent ignore
- **PDF compact fonts prefer Songti/STSong**: heavy CJK leads (PingFang / Noto Sans CJK) rewrite to subsettable faces; default `ADUPORT_PDF_FONT_FAMILY` / `settings.pdf_font_family` is `Songti SC, STSong, Noto Sans CJK SC, sans-serif` (compact still rewrites heavier overrides)
- **PDF `<code>` / `<pre>` font stack**: `Courier New, Songti SC, monospace`; CSS font stacks are not `html.escape(..., quote=True)` (entities broke WeasyPrint and re-introduced Nanum/Hiragino)
- **Bool Form values**: illegal tokens (`csv_table_only=bogus` etc.) return 422; accept `true/false/1/0/yes/no/on/off`
- **Int Form values**: illegal `rag_max_chars` / `rag_chunk_overlap` / `ocr_max_pages` return 422 (no silent ignore)
- **CSV `_CONTENT` banner skip on import**: `# [AduPort] …` label no longer rehydrates as a fake H1
- **Page-grade `--init-business`**: empty business worksheet intake (`CORPUS_READ` stays false)
- **`claim_policy` / `text_markers`**: csv/txt demoted from `full`; jpg/png excluded from content-success claims
- **Prebuilt `aduport-native` wheels**: CI matrix (manylinux x86_64/aarch64 + macOS) via `.github/workflows/native-wheels.yml`; install without Rust using `scripts/install_native_wheel.sh` (`--release` / `--dir` / `--tarball`); offline pack `scripts/pack_native_wheels.sh` + enterprise bundle `wheels/`
- `scripts/build_native.sh --prebuilt` tries Release wheels before compiling
- **ole_cfb fuzz baseline**: `backend/scripts/fuzz_ole_cfb.py` + `.github/workflows/fuzz.yml` (nightly / dispatch)
- **Session depth in `/api/health/detail`**: `session.hmac_session` / `revoke_backend` / `cookie_secure` / multi-worker hints
- **Real `.msg` CFB fixtures**: `build_minimal_msg` in tests (no ole monkeypatch假绿)
- **`ADUPORT_SESSION_SECRET` / `session_secret`**: HMAC fail-closed (v13 P1-1; no hardcoded fallback)
- **One-click start**: root `./start.sh` (Docker Compose or `--local`)
- **Pinned rustup installer**: `scripts/install_rustup_pinned.sh` (SHA256; toolchain 1.88.0)
- **Wheel artifact audit**: `scripts/audit_native_wheels.py` (native-wheels CI pack step)
- **Enterprise delivery docs**: `enterprise/delivery/` (COMMERCIAL_LICENSE / SLA / PRICING / OFFLINE / OPS)

### Changed
- Product boundary unchanged: main `AduPort` PyPI stays Python-only; native ships as GitHub Release assets / Docker / source build
- **CSV notes**: pure-table export via `csv_table_only` (HTTP Form / CLI / Python); default still writes `_CONTENT` + banner
- **`stuck_convert_threads`**: counted in job store stats after `job_timeout`; also exposed as Prometheus counter `aduport_stuck_convert_threads` + metrics alert (thread still not killable; prefer `ADUPORT_CONVERT_ISOLATE`)
- **DOCX tables**: `gridBefore` / `hMerge` map to column offset and `col_span`; vertical-merge continuation text stays on the origin cell; empty grid slots are real cells (not false `covered`), so HTML round-trips keep full column width
- **HTML span tables**: empty cells emit `<td></td>` / `<th></th>`; rowspan occupancy is tracked so paste width stays rectangular
- **ODT formulas**: embedded MathML / formula objects become `MathBlock`; anydoc `$...$` is lifted the same way
- **Charts**: Markdown export writes category × series values as a pipe table (axis titles kept)
- **PPTX OLE**: embeddings that anydoc stored as `application/vnd.ms-ole-object` images become `OleObject` labels
- **DOCX OLE**: skip the bare `w:object` wrapper so each embed yields one `OleObject` (ProgID + payload)
- **Isolate pool Prometheus gauges**: `aduport_convert_isolate_enabled` / `_started` / `_workers` (same fields as `/api/health/detail.isolate`)
- **Job deadline vs isolate**: with `ADUPORT_CONVERT_ISOLATE` (default on) the job runner calls `convert_isolated` on its own thread; a timeout SIGTERMs pool workers (or the popen session) instead of abandoning an extra daemon. In-process converts (`CONVERT_ISOLATE=false`) still cannot be killed and still increment `stuck_convert_threads`
- **`ADUPORT_PDF_FONT_COMPACT`**: default on; pairs with subsettable CJK preference above
- **Job tokens / API key**: frontend memory-only; HttpOnly cookie path clears durable material
- **`ADUPORT_COOKIE_SECURE`**: configurable Secure cookie (default on)
- **Preview XSS**: App preview uses sandboxed `iframe` `srcdoc`
- **Tenant-scoped persistence**: job results / object archive / spill paths; atomic writes; list_recent passes tenant_id
- **Container / CI harden**: nginx-unprivileged; compose `cap_drop` + `no-new-privileges`; workflow `permissions: contents: read`; `pip-audit --strict`
- **Enterprise install**: generates `SESSION_SECRET`; refuses `REQUIRE_AUTH=0`
- **Config**: Literal/ge/le on key fields; sensitive values as `SecretStr` via `secret_plain()`
- **CI supply chain (v13 P2-5)**: all workflow `uses:` pinned to full commit SHAs (actions/* + dtolnay/Swatinem/PyO3/pypa)

## [0.16.0] - 2026-09-01

anydoc 源码级融入 + 自有 PDF 文本层依赖（相对 0.11 线累计至当前包版本）。

### Added
- **Vendored anydoc (Firecrawl) as `aduport_native._doc`**: Rust sources under `native/anydoc/`; maturin builds an in-process abi3 extension (**not** shipped on PyPI — use Docker `INSTALL_NATIVE=1` or `scripts/build_native.sh`)
- **Importer bridge**: fast path (`to_markdown_bytes`) and model path (`to_document` → `DocumentModel`); hosted OCR removed
- **Ops**: `ADUPORT_ANYDOC_*` (incl. `FALLBACK` / `SNIFF`); `/health` `anydoc_available` / `anydoc_version`; `/formats` only/led lists
- **Docker multi-stage**: install only `aduport_native*.whl` when `INSTALL_NATIVE=1`
- **Scripts**: `build_native.sh`, `sync_anydoc.sh` (pinned via `UPSTREAM_PIN`), `docker_build_safe.sh` (ASCII-safe context)
- **Core PDF text-layer**: `pypdfium2>=4.30` first-class (fixes digital PDF 0-char in slim/dev)
- **Telemetry**: `aduport_anydoc_hit_rate`, `aduport_anydoc_channel_share`, `aduport_anydoc_hits_by_channel{channel=}`
- **DOCX product switches**: Form `docx_theme`; `ADUPORT_DOCX_EXPORT_COMMENTS` / `ADUPORT_HEADING_NUMBERING`; `/health` exposes theme/comments/numbering
- **XLSX value export**: `Table.cells` fallback; import metadata `export_policy=values_only`
- **OCR readiness**: `/health` `ocr_ready` + `ocr.install_hint` when engines missing
- **Product surface**: Web PDF/DOCX advanced options; job `callback_url` webhook; `ADUPORT_JOB_RESULT_DIR` result blobs; CLI PDF/DOCX parity; `docs/document-model.md` + `ENTERPRISE.md`
- **Vertical depth (v1.5)**: RTF export target; PDF two-column heuristic (`ADUPORT_PDF_TEXT_MULTICOLUMN`); xlsx sheet names + header bold styles; PPTX multi-textbox layout; tenant `daily_quota` + `metrics.alerts`; Docker `INSTALL_OCR=1` build-arg
- **Ops & quality (v1.6)**: `docs/ops/prometheus/` Grafana dashboard + alert rules; metrics `aduport_ocr_ready`, `aduport_conversions_failed_by_code`; golden matrix CI `test_golden_matrix.py`
- **P2-4 breadth**: default `ADUPORT_ANYDOC_FORMATS` adds **epub** + **csv** (anydoc-first, native fallback); fixture tests in `test_anydoc_bridge.py`
- **Comment export**: `html_render` renders `<aside class="comment">` for HTML/EPUB/MOBI/AZW3 (`test_html_comment_export.py`)
- **§2 vertical (v1.8)**: enterprise `INSTALL_OCR=1` + `ADUPORT_OCR_ENABLED` default; PDF/PPTX Comment; Prometheus templates
- **AST depth (v1.9)**: `Revision`; DOCX `section_props`; `Image.wrap`; xlsx formulas + pivot metadata; PDF layout meta; PPTX layouts; EPUB DRM detect (`test_accepted_depth.py`)
- **Registry fix**: restore full exporter/importer registration (xlsx/odt/csv/json/latex/rtf + anydoc wiring)

### Changed
- Default `ADUPORT_ANYDOC_FORMATS` includes **doc** + **pptx** + **epub** + **csv** (Rust fast→md); **xlsx** stays native (header-row); **pdf** stays AduPort engines
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
- **HTML KaTeX** CDN auto-render for MathBlock (`ADUPORT_HTML_KATEX`)
- **MOBI text fallback**: PalmDB extract when docling missing/fails
- **Table header switch**: `ADUPORT_TABLE_FIRST_ROW_HEADER` (default true)
- CI: ruff hard-fail, coverage ≥70%, pip-audit (advisory)

### Changed
- Version 0.11.0

## [0.10.0] - 2026-08-31

### Added
- **KF8 / AZW3** export with EXTH + embedded image records (`recindex`)
- **True Word comments**: `word/comments.xml` + commentRange markers (Review pane)
- **边解析边转**: chunked MD/TXT/HTML convert with spill (`ADUPORT_STREAM_CONVERT`)

### Changed
- Version 0.10.0

## [0.9.0] - 2026-08-31

### Added
- **MOBI export** (minimal PalmDOC/MOBI, UTF-8 HTML payload)
- **PDF text-layer parallel extract** (`ADUPORT_PDF_TEXT_PARALLEL` / `PDF_TEXT_WORKERS`)
- **Gzip result cache** (`ADUPORT_CACHE_GZIP`, min size threshold)
- **Startup warmup** for docling converters + WeasyPrint (`ADUPORT_WARMUP_ON_STARTUP`)
- **HTML importer harden**: nested div/section, blockquote/hr, script skip, stack pop-to-match

### Changed
- Version 0.9.0 — targets now 8×8 (full matrix for registered formats)

## [0.8.0] - 2026-08-31

### Added
- **PPTX export** via python-pptx (title + content slides)
- **Streaming upload**: chunked `UploadFile.read` → SpillBuffer (`read_upload_streaming`)
- **DOCX themes** (`ADUPORT_DOCX_THEME=default|academic|minimal`) + **Comment** AST → 批注 section
- **ListItem.children** for multi-paragraph list items
- Cache hit can skip magic/virus re-check (`ADUPORT_CACHE_SKIP_REVALIDATE`)
- Rate-limit IP key pruning; audit detail path sanitization
- GitHub Actions CI (pytest + frontend build)

### Changed
- Version bump to 0.8.0; targets now include pptx (8×7)

## [0.7.0] - 2026-08-31

### Added
- **Target formats**: `html` / `txt` / `epub` exporters (8×6 matrix vs prior 8×3)
- **PDF layout**: page size, orientation, header/footer, page numbers, TOC (`ADUPORT_PDF_*` + form fields)
- **Remote images**: SSRF-safe HTTP(S) fetch into `Image.data` (`ADUPORT_FETCH_REMOTE_IMAGES`)
- **Disk spill**: async job payloads ≥ threshold written to 0600 temp files (`ADUPORT_SPILL_THRESHOLD_MB`)
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
- **病毒扫描钩子**：`ADUPORT_VIRUS_SCAN` + ClamAV（`clamscan`/`clamdscan`）
- **Web**：中英切换、队列拖拽排序、服务端任务结果页（`GET /api/jobs`）
- **OCR 产品化**：`/api/health.ocr` 标注「扫描 PDF → 结构化 Markdown」卖点
- Tests: `test_v05_upgrade.py`

## [0.4.0] - 2026-08-31

### Added (Ch.4 路线图续作)
- **Async jobs:** `POST /api/jobs` → poll `GET /api/jobs/{id}` → `GET /api/jobs/{id}/result`；Web UI 进度百分比
- **Formats:** PPTX / EPUB 导入（需 `pip install 'aduport[office]'` / docling）
- **Security:** 可选 HTTP Basic Auth（`ADUPORT_BASIC_AUTH=user:pass`，可与 API-Key 二选一通过）
- **Ops:** 异步任务内存预算（`ADUPORT_MEMORY_BUDGET_MB`）+ 健康检查暴露 jobs 统计
- Tests: +9（`test_v04_upgrade.py`）

## [0.3.0] - 2026-08-31

### Added (Ch.4 完善·优化·升级)
- **Security:** magic-bytes + extension whitelist; per-IP rate limit; JSONL audit log; temp files `chmod 0600`
- **Formats:** TXT / HTML importers; `/api/formats` returns `source` / `target` lists
- **Ops:** SHA256 result cache (TTL + size cap); Prometheus `/api/metrics`; enhanced `/api/health`
- **Architecture:** importer/exporter plugin registry (replaces hard-coded convert branches)
- **Web UI:** conversion history (localStorage), download-all, source/target format split (TXT/HTML import)
- **PDF:** optional CJK first-line indent via `ADUPORT_PDF_CJK_TYPESET`
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
