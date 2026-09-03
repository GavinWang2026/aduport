"""Application settings loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_CANDIDATES = (
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent.parent.parent / ".env",
    Path.cwd() / ".env",
)


def _find_env_file() -> str | None:
    for path in _ENV_CANDIDATES:
        if path.is_file():
            return str(path)
    return ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MDBRIDGE_",
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "md-bridge"
    debug: bool = False
    max_upload_mb: int = 100
    # D-24: abort stalled chunked uploads (slowloris).
    upload_timeout_seconds: float = 60.0
    # D-27: OpenAPI /docs is debug-only unless explicitly enabled.
    expose_api_docs: bool = False
    cors_origins: str = "local"
    cors_allow_credentials: bool = False

    ocr_enabled: bool = False
    # OCR router: auto | mineru | paddleocr_vl | docling | none
    ocr_engine: str = "auto"
    # balanced → MinerU first; accurate → PaddleOCR-VL first
    ocr_mode: str = "balanced"
    # Second try when primary fails / output too short
    ocr_fallback: str = "paddleocr_vl"
    # Soft quality gate (chars across nodes); 0 = disable
    ocr_min_chars: int = 40
    ocr_timeout_seconds: float = 300.0
    # Cap pages fed to OCR engines (0 = unlimited). Full textbooks otherwise hang.
    ocr_max_pages: int = 20
    mineru_bin: str = "mineru"
    mineru_backend: str = "pipeline"
    # Formula OCR pulls Unimernet + transformers; default off for lighter installs
    mineru_formula: bool = False
    mineru_table: bool = True
    # Writable cache for PaddleX / ModelScope (empty = use process HOME)
    ocr_home: str = ""
    # PaddleOCR-VL: auto | true | false. auto = off on macOS (SIGTRAP in short workers)
    ocr_allow_paddle: str = "auto"

    # PDF import: auto | text | docling | ocr | mineru | paddleocr_vl
    # Tri-state product view (v8 §B):
    #   text   = pypdfium2 text layer only
    #   docling = optional layout/AI (requires models)
    #   ocr    = enterprise scan path (MinerU / Paddle); aliases mineru|paddleocr_vl
    #   auto   = text-richness gate → OCR (if enabled) → docling → text
    pdf_engine: str = "auto"
    # Embed %%MDBRIDGE-AST sidecar after PDF %%EOF (debug/round-trip only).
    # Default off — sidecar is not layout fidelity; do not count as PDF 保真.
    pdf_ast_sidecar: bool = False
    pdf_text_min_chars: int = 80
    pdf_docling_tables: bool = True
    docling_artifacts_path: str = ""
    # Optional Hugging Face mirror (e.g. https://hf-mirror.com) for docling model download
    hf_endpoint: str = ""
    # Never attempt Hub download at runtime (fail fast → text fallback)
    docling_offline: bool = True
    # Soft timeout for a single docling.convert call (seconds)
    docling_timeout_seconds: float = 60.0

    # --- anydoc native parser (vendored Rust, abi3) ---
    # Master switch for the high-fidelity Office / EPUB / spreadsheet importer.
    anydoc_enabled: bool = True
    # Comma-separated allow-list of *shared* formats anydoc may accelerate.
    # anydoc-only formats (xls, xlsb, ods, odp, ppt, rtf, odt, docm, …) are
    # always owned when enabled; this list governs formats that also have a
    # native importer. xlsx is intentionally excluded (kept native for header
    # inference). "*" enables every supported shared format.
    # Shared formats anydoc may accelerate. v9 P1: docx 交回 native（图片/列表/span
    # 高保真管线）；legacy OLE `doc` 与 ppt 系仍走 Rust 快通道。xlsx 保持 native。
    anydoc_formats: str = "doc,rtf,odt,pptx,ppt,pps,pot,pptm,ppsx,ppsm,docm,epub,csv"
    # Route Markdown targets straight to anydoc's own GFM (no fidelity loss).
    anydoc_fast_markdown: bool = True
    # Recoverable AnydocFallback → native importer (False = surface the decline).
    anydoc_fallback: bool = True
    # Content sniff via native format_from_bytes when extension is missing/wrong.
    anydoc_sniff: bool = True

    def anydoc_format_list(self) -> set[str]:
        """Parsed allow-list of shared formats anydoc may own."""
        raw = (self.anydoc_formats or "").strip()
        if raw == "*":
            return {"*"}
        return {f.strip().lower().lstrip(".") for f in raw.split(",") if f.strip()}

    pdf_font_family: str = "Noto Sans CJK SC, Helvetica Neue, Arial, sans-serif"
    pdf_custom_css: str = ""
    # Chinese typesetting enhancements for PDF
    pdf_cjk_typeset: bool = True
    # Explicit CJK font file for pdf_simple embedding (empty = auto-discover)
    pdf_cjk_font_path: str = ""
    # When CJK cannot be embedded: error | ascii
    pdf_cjk_fallback: str = "error"
    # PDF layout options (WeasyPrint @page)
    pdf_page_size: str = "A4"
    pdf_orientation: str = "portrait"  # portrait | landscape
    pdf_header: str = ""
    pdf_footer: str = ""
    pdf_page_numbers: bool = True
    pdf_toc: bool = False
    # Remap PEP textbook math-font CJK→Latin (destructive on normal names; default off)
    pdf_pep_font_fix: bool = False

    # Remote images (SSRF-guarded)
    fetch_remote_images: bool = True
    fetch_remote_timeout: float = 8.0
    fetch_remote_max_bytes: int = 5 * 1024 * 1024

    # Spill large payloads to disk (0 = disable spill, always keep in RAM)
    spill_threshold_mb: int = 8

    # Cache: on hit, skip magic/virus re-check (content already validated once within TTL)
    cache_skip_revalidate: bool = True

    # DOCX theme: default | academic | minimal
    docx_theme: str = "default"
    # Insert a Word TOC field near the top when the document has headings
    docx_auto_toc: bool = True
    # Emit Word Review comments.xml when AST has Comment nodes
    docx_export_comments: bool = True
    # Prefix headings with 1 / 1.1 / 1.1.1 on export
    heading_numbering: bool = False

    # Image compress / downsample before export (requires Pillow)
    image_compress: bool = False
    image_max_edge: int = 1600
    image_jpeg_quality: int = 82

    # Tables: treat first row as <th> (MD/GFM convention). Set false for all <td>.
    table_first_row_header: bool = True
    # v9 P2: when True, span tables export as HTML (<td colspan>) instead of flat GFM;
    # also skips anydoc fast→md for office sources so the model path can emit spans.
    html_table_for_span: bool = True

    # v9 P3: LLM-friendly Markdown — whitespace normalize, strip base64 images,
    # optional YAML front-matter (title + importer metadata).
    llm_mode: bool = False
    llm_mode_strip_images: bool = True
    llm_mode_front_matter: bool = True

    # HTML export: inject KaTeX CDN for MathBlock rendering
    html_katex: bool = True

    # 边解析边转（大 MD/TXT/HTML）
    stream_convert: bool = True
    stream_chunk_chars: int = 48_000
    stream_min_bytes: int = 256_000  # below this, use normal full AST path

    api_key: str = ""

    max_batch_files: int = 20
    max_batch_total_mb: int = 200
    batch_workers: int = 3
    # Sync/API convert ThreadPool size (0 = reuse batch_workers). T38 engine pool.
    convert_pool_workers: int = 0
    # XLSX workbook sheet hard cap (data + Content + meta). Was hard-coded 8.
    xlsx_max_sheets: int = 64
    # Emit hidden `_AST` meta sheet for non-table nodes (Comment/Chart/…).
    # Default off — table-body fidelity only; opt-in for AST sidecar round-trip.
    xlsx_ast_sheet: bool = False

    # Security / ops
    rate_limit_per_minute: int = 60
    # Trusted reverse-proxy hop count for X-Forwarded-For (v6/T3-#18 / D-19).
    # 0 = ignore XFF and use socket peer; 1+ = take the client IP that many hops from the right.
    trusted_proxy_hops: int = 0
    cache_enabled: bool = True
    cache_max_entries: int = 128
    # v6/T1-#6: entry-count eviction alone let 128 x 4MB blobs grow to ~512MB.
    # Byte watermark is the real bound; 256MB default keeps a 4-worker host safe.
    cache_max_bytes: int = 256 * 1024 * 1024
    cache_ttl_seconds: int = 3600
    cache_gzip: bool = True
    cache_gzip_min_bytes: int = 2048
    audit_enabled: bool = True
    audit_log_path: str = "logs/audit.jsonl"
    audit_max_bytes: int = 50 * 1024 * 1024
    audit_backup_count: int = 10
    # Magic-bytes upload check
    magic_bytes_check: bool = True

    # PDF text-layer extraction
    # Kept for env compatibility; ignored at runtime — PDFium is not thread-safe
    # (macOS libpdfium.dylib aborts with EXC_BREAKPOINT / SIGTRAP under threads).
    pdf_text_parallel: bool = False
    pdf_text_workers: int = 4

    # Startup warmup (docling + WeasyPrint)
    warmup_on_startup: bool = True
    warmup_force: bool = False

    # Async jobs
    job_ttl_seconds: int = 3600
    job_max_entries: int = 256
    # Max queued+running jobs before 429 (bounds ThreadPoolExecutor backlog)
    job_queue_max: int = 64
    # 0 = unlimited in-flight upload budget for async jobs
    memory_budget_mb: int = 512
    # Persist finished job metadata (empty = memory only)
    job_db_path: str = "data/jobs.sqlite3"
    # Persist result blobs beside metadata (empty = memory/spill only; survives restart)
    job_result_dir: str = "data/job_results"
    # Long-term archive URI: file:///path or s3://bucket/prefix (s3 needs boto3 extra)
    object_storage_uri: str = ""
    job_archive_retention_days: int = 30
    # EPUB zip passphrase (password-protected zips only; not Adobe/LCP DRM)
    epub_passphrase: str = ""

    # Job completion webhooks (POST JSON to callback_url)
    webhook_enabled: bool = True
    webhook_timeout_seconds: float = 10.0

    # Soft warn / reject oversized DOCX AST (0 = disabled)
    docx_max_nodes: int = 200_000  # v6/T2-#13: was 50_000; 10MB md blew the cap

    # PDF text-layer: try left/right half merge when both columns look populated
    pdf_text_multicolumn: bool = True

    # Optional Redis URL for multi-replica rate limiting (empty = in-process)
    redis_url: str = ""

    # Run each conversion in a child process (survives native SIGABRT)
    convert_isolate: bool = True
    convert_timeout_seconds: int = 600
    # v6/T2-#12: persist a ProcessPool of convert workers (reuse interpreter +
    # cache). Set True to restore the old per-request ``python -m convert_worker``
    # fork (crash-debug / platforms where spawn pools hang).
    convert_isolate_fork: bool = False
    # Recycle isolate workers after N jobs to bound native-leak RSS (0 = never).
    convert_isolate_max_tasks: int = 64
    # v8 §C: trusted clients skip the pool (in-process) to avoid +~180ms spawn
    # tax and keep result-cache hits. Comma-separated client ids from auth
    # (tenant_id / api key label). Empty = nobody trusted (all go through pool).
    convert_trusted_clients: str = ""
    # Master switch for trusted in-process path (False = always isolate when on).
    convert_trusted_inprocess: bool = True

    # Optional LibreOffice headless bridge (v8 §D) — never bundled; call-out only.
    libreoffice_enabled: bool = False
    libreoffice_bin: str = ""
    libreoffice_timeout_seconds: float = 120.0

    # Optional virus scan (ClamAV)
    virus_scan: bool = False
    virus_scan_fail_closed: bool = True
    virus_scan_timeout_seconds: int = 60

    # Optional HTTP Basic Auth (user:pass). Empty = disabled.
    basic_auth: str = ""

    # Fail closed when no api_key / api_keys / basic_auth are configured.
    # Set MDBRIDGE_REQUIRE_AUTH=0 (or MDBRIDGE_DEV_OK=1) for local demo / tests.
    require_auth: bool = True

    # Require X-Job-Token / ?token= for GET /jobs/{id} and /result (create returns token once).
    job_token_required: bool = True

    # Readiness (/api/health/ready): fail when vendored native wheel is missing.
    # Docker images set MDBRIDGE_NATIVE_REQUIRED=1 when INSTALL_NATIVE=1.
    native_required: bool = False

    # Multi-tenant API keys (P0-3). Formats:
    #   JSON file path, or inline JSON array:
    #   [{"tenant_id":"acme","api_key":"sk-…","rate_limit":120}]
    #   Or compact: "acme:sk-aaa,beta:sk-bbb"
    # When empty, falls back to single settings.api_key (tenant_id="default").
    api_keys: str = ""

    # Enterprise license (one-machine-one-key). Off by default for OSS self-host.
    license_required: bool = False
    license_key: str = ""
    license_file: str = ""
    license_secret: str = ""

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def api_docs_enabled(self) -> bool:
        return bool(self.debug or self.expose_api_docs)

    @property
    def max_batch_total_bytes(self) -> int:
        return self.max_batch_total_mb * 1024 * 1024

    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if raw in ("", "local"):
            return [
                "http://localhost:7173",
                "http://127.0.0.1:7173",
                "http://localhost:8080",
                "http://127.0.0.1:8080",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ]
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()
