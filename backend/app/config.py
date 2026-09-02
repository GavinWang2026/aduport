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

    # PDF import: auto | text | docling | mineru | paddleocr_vl
    # auto = use embedded text when rich enough, else OCR (if enabled) / docling
    pdf_engine: str = "auto"
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
    # Shared formats anydoc leads on (exclusive anydoc-only siblings always lead).
    # doc/pptx: Rust OLE/OOXML path preferred over heavy docling for →md fast channel.
    # xlsx omitted on purpose (native header-row inference). epub/csv now default-on (P2-4).
    anydoc_formats: str = "doc,docx,rtf,odt,pptx,ppt,pps,pot,pptm,ppsx,ppsm,docm,epub,csv"
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

    # Security / ops
    rate_limit_per_minute: int = 60
    cache_enabled: bool = True
    cache_max_entries: int = 128
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
    docx_max_nodes: int = 50_000

    # PDF text-layer: try left/right half merge when both columns look populated
    pdf_text_multicolumn: bool = True

    # Optional Redis URL for multi-replica rate limiting (empty = in-process)
    redis_url: str = ""

    # Run each conversion in a child process (survives native SIGABRT)
    convert_isolate: bool = True
    convert_timeout_seconds: int = 600

    # Optional virus scan (ClamAV)
    virus_scan: bool = False
    virus_scan_fail_closed: bool = True
    virus_scan_timeout_seconds: int = 60

    # Optional HTTP Basic Auth (user:pass). Empty = disabled.
    basic_auth: str = ""

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
