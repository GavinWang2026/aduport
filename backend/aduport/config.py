"""Application settings loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

def _expose_homebrew_libs() -> None:
    """Let WeasyPrint dlopen Homebrew's Pango/GObject by filename.

    The libraries are already at ``/opt/homebrew/lib/libgobject-2.0.0.dylib``.
    dyld does not search that directory unless it is on the fallback path,
    so PDF export otherwise drops to the simplified writer.
    """
    import os
    import sys

    if sys.platform != "darwin":
        return
    roots = [path for path in ("/opt/homebrew/lib", "/usr/local/lib") if os.path.isdir(path)]
    if not roots:
        return
    key = "DYLD_FALLBACK_LIBRARY_PATH"
    current = [part for part in os.environ.get(key, "").split(":") if part]
    os.environ[key] = ":".join(roots + [part for part in current if part not in roots])


_expose_homebrew_libs()


_ENV_CANDIDATES = (
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent.parent.parent / ".env",
    Path.cwd() / ".env",
)


def _migrate_legacy_env_prefix() -> None:
    """Map deprecated ``MDBRIDGE_*`` vars onto ``ADUPORT_*`` when unset.

    Covers both process env and ``.env`` files so old deploys keep working.
    Explicit ``ADUPORT_*`` always wins.
    """
    import os

    def adopt(key: str, value: str) -> None:
        if not key.startswith("MDBRIDGE_"):
            return
        new_key = "ADUPORT_" + key[len("MDBRIDGE_") :]
        if new_key not in os.environ:
            os.environ[new_key] = value

    for key, value in list(os.environ.items()):
        adopt(key, value)

    for path in _ENV_CANDIDATES:
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                adopt(key, value)
        except OSError:
            continue
        break


_migrate_legacy_env_prefix()


def _find_env_file() -> str | None:
    for path in _ENV_CANDIDATES:
        if path.is_file():
            return str(path)
    return ".env"


def _empty_secret() -> SecretStr:
    return SecretStr("")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ADUPORT_",
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AduPort"
    debug: bool = False
    max_upload_mb: int = Field(default=100, ge=1, le=2048)
    # D-24: abort stalled chunked uploads (slowloris).
    upload_timeout_seconds: float = Field(default=60.0, ge=1.0, le=3600.0)
    # D-27: OpenAPI /docs is debug-only unless explicitly enabled.
    expose_api_docs: bool = False
    cors_origins: str = "local"
    cors_allow_credentials: bool = False

    ocr_enabled: bool = False
    # OCR router: auto | mineru | paddleocr_vl | docling | none
    ocr_engine: Literal["auto", "mineru", "paddleocr_vl", "docling", "none"] = "auto"
    # balanced → MinerU first; accurate → PaddleOCR-VL first
    ocr_mode: Literal["balanced", "accurate"] = "balanced"
    # Second try when primary fails / output too short
    ocr_fallback: Literal["auto", "mineru", "paddleocr_vl", "docling", "none"] = "paddleocr_vl"
    # Soft quality gate (chars across nodes); 0 = disable
    ocr_min_chars: int = Field(default=40, ge=0, le=1_000_000)
    ocr_timeout_seconds: float = Field(default=300.0, ge=1.0, le=7200.0)
    # Cap pages fed to OCR engines (0 = unlimited). Full textbooks otherwise hang.
    ocr_max_pages: int = Field(default=20, ge=0, le=10_000)
    mineru_bin: str = "mineru"
    mineru_backend: str = "pipeline"
    # Formula OCR pulls Unimernet + transformers; default off for lighter installs
    mineru_formula: bool = False
    mineru_table: bool = True
    # Writable cache for PaddleX / ModelScope (empty = use process HOME)
    ocr_home: str = ""
    # PaddleOCR-VL: auto | true | false. auto = off on macOS (SIGTRAP in short workers)
    ocr_allow_paddle: Literal["auto", "true", "false"] = "auto"

    # RAG connector export (target_format=rag)
    rag_max_chars: int = Field(default=1200, ge=64, le=100_000)
    rag_chunk_overlap: int = Field(default=150, ge=0, le=10_000)
    # Comma-separated encodings tried after UTF-8. Latin-1 is not included:
    # it accepts every byte string, including the garbage-byte probe.
    encoding_fallback: str = "gb18030,utf-16"
    # Extra directories that local Markdown images may be read from.
    # The source file's directory is always allowed. Empty does not open the filesystem.
    media_roots: str = ""
    media_max_bytes: int = Field(default=8_000_000, ge=1024, le=50_000_000)
    # Built-in embedding (default off at convert; backend always selectable)
    # hash = deterministic builtin; fastembed = optional pip; none = disable
    embed_backend: Literal["hash", "fastembed", "none"] = "hash"
    embed_model: str = ""
    embed_dim: int = Field(default=64, ge=8, le=4096)
    embed_default: bool = False
    embed_batch_max: int = Field(default=512, ge=1, le=10_000)
    # Local JSONL vector store directory (empty = do not persist)
    vector_dir: str = ""
    vector_collection: str = "default"
    # Keep job input after success so POST /jobs/{id}/reprocess can re-OCR
    job_retain_input_on_done: bool = True

    # PDF import: auto | text | docling | ocr | mineru | paddleocr_vl
    pdf_engine: Literal["auto", "text", "docling", "ocr", "mineru", "paddleocr_vl"] = "auto"
    # Embed %%ADUPORT-AST sidecar after PDF %%EOF (debug/round-trip only).
    pdf_ast_sidecar: bool = False
    pdf_text_min_chars: int = Field(default=80, ge=0, le=1_000_000)
    # Prefer docling for auto when local models are ready (v15 §4.2).
    # Falls back to flat text-layer with fidelity_warnings if docling fails.
    pdf_auto_prefer_docling: bool = True
    pdf_docling_tables: bool = True
    docling_artifacts_path: str = ""
    # Optional Hugging Face mirror (e.g. https://hf-mirror.com) for docling model download
    hf_endpoint: str = ""
    # Never attempt Hub download at runtime (fail fast → text fallback)
    docling_offline: bool = True
    # Soft timeout for a single docling.convert call (seconds)
    docling_timeout_seconds: float = Field(default=60.0, ge=1.0, le=3600.0)

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
    # Text-layer PDF → Markdown via anydoc (pdf-inspector). Scanned / NeedsOcr
    # declines fall through to AduPort PDF/OCR. Never enables model import.
    anydoc_pdf_markdown: bool = True
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

    # Prefer subsettable CJK (Songti/STSong) before full-embed faces (PingFang /
    # Noto Sans CJK). Compact mode also rewrites heavy leads via prefer_subsettable_cjk.
    # Override with ADUPORT_PDF_FONT_FAMILY if needed.
    pdf_font_family: str = "Songti SC, STSong, Noto Sans CJK SC, sans-serif"
    # When true, export keeps only the first named face + one generic from the
    # stack, and swaps heavy CJK leads for Songti/STSong when possible.
    pdf_font_compact: bool = True
    pdf_custom_css: str = ""
    # Chinese typesetting enhancements for PDF
    pdf_cjk_typeset: bool = True
    # Explicit CJK font file for pdf_simple embedding (empty = auto-discover)
    pdf_cjk_font_path: str = ""
    # When CJK cannot be embedded: error | ascii
    pdf_cjk_fallback: Literal["error", "ascii"] = "error"
    # PDF layout options (WeasyPrint @page)
    pdf_page_size: Literal["A4", "A3", "A5", "Letter", "Legal"] = "A4"
    pdf_orientation: Literal["portrait", "landscape"] = "portrait"
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
    spill_threshold_mb: int = Field(default=8, ge=0, le=4096)

    # Cache: on hit, skip magic/virus re-check (content already validated once within TTL)
    cache_skip_revalidate: bool = False

    # Accept job token via ?token= query (deprecated; logs may capture tokens). Default off.
    job_token_query: bool = False

    # DOCX theme: default | academic | minimal
    docx_theme: Literal["default", "academic", "minimal"] = "default"
    # Insert a Word TOC field near the top when the document has headings
    docx_auto_toc: bool = False
    # Emit Word Review comments.xml when AST has Comment nodes
    docx_export_comments: bool = True
    # Prefix headings with 1 / 1.1 / 1.1.1 on export
    heading_numbering: bool = False

    # Image compress / downsample before export (requires Pillow)
    image_compress: bool = False
    image_max_edge: int = Field(default=1600, ge=64, le=16_000)
    image_jpeg_quality: int = Field(default=82, ge=1, le=100)

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
    # When true, LaTeX export prefixes each block with a round-trip comment.
    latex_plain_comments: bool = True

    # 边解析边转（大 MD/TXT/HTML）
    stream_convert: bool = True
    stream_chunk_chars: int = Field(default=48_000, ge=1000, le=5_000_000)
    stream_min_bytes: int = Field(default=256_000, ge=0, le=500_000_000)

    api_key: SecretStr = Field(default_factory=_empty_secret)

    max_batch_files: int = Field(default=20, ge=1, le=500)
    max_batch_total_mb: int = Field(default=200, ge=1, le=10_000)
    batch_workers: int = Field(default=3, ge=1, le=64)
    # Sync/API convert ThreadPool size (0 = reuse batch_workers). T38 engine pool.
    convert_pool_workers: int = Field(default=0, ge=0, le=64)
    # XLSX workbook sheet hard cap (data + Content + meta). Was hard-coded 8.
    xlsx_max_sheets: int = Field(default=64, ge=1, le=512)
    # Emit hidden `_AST` meta sheet for non-table nodes (Comment/Chart/…).
    # Default off — table-body fidelity only; opt-in for AST sidecar round-trip.
    xlsx_ast_sheet: bool = False

    # Security / ops
    rate_limit_per_minute: int = Field(default=60, ge=0, le=1_000_000)
    # Trusted reverse-proxy hop count for X-Forwarded-For (v6/T3-#18 / D-19).
    # 0 = ignore XFF and use socket peer; 1+ = take the client IP that many hops from the right.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=16)
    cache_enabled: bool = True
    cache_max_entries: int = Field(default=128, ge=1, le=100_000)
    # v6/T1-#6: entry-count eviction alone let 128 x 4MB blobs grow to ~512MB.
    # Byte watermark is the real bound; 256MB default keeps a 4-worker host safe.
    cache_max_bytes: int = Field(default=256 * 1024 * 1024, ge=0)
    cache_ttl_seconds: int = Field(default=3600, ge=0, le=7 * 24 * 3600)
    cache_gzip: bool = True
    cache_gzip_min_bytes: int = Field(default=2048, ge=0)
    audit_enabled: bool = True
    audit_log_path: str = "logs/audit.jsonl"
    audit_max_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    audit_backup_count: int = Field(default=10, ge=0, le=100)
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
    job_ttl_seconds: int = Field(default=3600, ge=0, le=30 * 24 * 3600)
    job_max_entries: int = Field(default=256, ge=1, le=100_000)
    # Max queued+running jobs before 429 (bounds ThreadPoolExecutor backlog)
    job_queue_max: int = Field(default=64, ge=1, le=10_000)
    # 0 = unlimited in-flight upload budget for async jobs
    memory_budget_mb: int = Field(default=512, ge=0, le=1_000_000)
    # Persist finished job metadata (empty = memory only)
    job_db_path: str = "data/jobs.sqlite3"
    # Persist result blobs beside metadata (empty = memory/spill only; survives restart)
    job_result_dir: str = "data/job_results"
    # Long-term archive URI: file:///path or s3://bucket/prefix (s3 needs boto3 extra)
    object_storage_uri: str = ""
    job_archive_retention_days: int = Field(default=30, ge=0, le=3650)
    # EPUB zip passphrase (password-protected zips only; not Adobe/LCP DRM)
    epub_passphrase: SecretStr = Field(default_factory=_empty_secret)

    # Job completion webhooks (POST JSON to callback_url)
    webhook_enabled: bool = True
    webhook_timeout_seconds: float = Field(default=10.0, ge=0.5, le=300.0)
    webhook_secret: SecretStr = Field(default_factory=_empty_secret)
    webhook_retries: int = Field(default=2, ge=0, le=10)

    # MCP local convert tool — comma-separated absolute roots (empty = cwd only)
    mcp_allowed_roots: str = ""
    mcp_allowed_extensions: str = (
        ".md,.markdown,.txt,.html,.htm,.docx,.doc,.pdf,.pptx,.ppt,"
        ".xlsx,.xls,.xlsb,.csv,.epub,.odt,.ods,.odp,.rtf,.json,.ipynb,"
        ".msg,.eml,.rss,.atom,.zip,.png,.jpg,.jpeg,.webp,.tif,.tiff"
    )

    # Soft warn / reject oversized DOCX AST (0 = disabled)
    docx_max_nodes: int = Field(default=200_000, ge=0)

    # PDF text-layer: try left/right half merge when both columns look populated
    pdf_text_multicolumn: bool = True

    # Optional Redis URL for multi-replica rate limiting (empty = in-process)
    redis_url: str = ""

    # Run each conversion in a child process (survives native SIGABRT)
    convert_isolate: bool = True
    convert_timeout_seconds: int = Field(default=600, ge=1, le=86_400)
    # v6/T2-#12: persist a ProcessPool of convert workers (reuse interpreter +
    # cache). Set True to restore the old per-request ``python -m convert_worker``
    # fork (crash-debug / platforms where spawn pools hang).
    convert_isolate_fork: bool = False
    # Recycle isolate workers after N jobs to bound native-leak RSS (0 = never).
    convert_isolate_max_tasks: int = Field(default=64, ge=0, le=100_000)
    # v8 §C: trusted clients skip the pool (in-process) to avoid +~180ms spawn
    # tax and keep result-cache hits. Comma-separated client ids from auth
    # (tenant_id / api key label). Empty = nobody trusted (all go through pool).
    convert_trusted_clients: str = ""
    # Master switch for trusted in-process path (False = always isolate when on).
    convert_trusted_inprocess: bool = True

    # Optional LibreOffice headless bridge (v8 §D) — never bundled; call-out only.
    libreoffice_enabled: bool = False
    libreoffice_bin: str = ""
    libreoffice_timeout_seconds: float = Field(default=120.0, ge=1.0, le=3600.0)

    # Optional virus scan (ClamAV)
    virus_scan: bool = False
    virus_scan_fail_closed: bool = True
    virus_scan_timeout_seconds: int = Field(default=60, ge=1, le=600)

    # Optional HTTP Basic Auth (user:pass). Empty = disabled.
    basic_auth: SecretStr = Field(default_factory=_empty_secret)

    # Fail closed when no api_key / api_keys / basic_auth are configured.
    # Set ADUPORT_REQUIRE_AUTH=0 (or ADUPORT_DEV_OK=1) for local demo / tests.
    require_auth: bool = True

    # Require X-Job-Token / ?token= for GET /jobs/{id} and /result (create returns token once).
    job_token_required: bool = True

    # Session cookie Secure flag (HTTPS-only). Default True; set False for plain-HTTP local demos.
    # Multi-worker: also set ADUPORT_SESSION_SECRET (≥16) + ADUPORT_REDIS_URL for HMAC + revoke/seats.
    cookie_secure: bool = True
    # HMAC secret for signed session cookies (≥16). Empty = opaque in-process sessions only
    # (never falls back to a hardcoded string — v13 P1-1).
    session_secret: SecretStr = Field(default_factory=_empty_secret)

    # Readiness (/api/health/ready): fail when vendored native wheel is missing.
    # Docker images set ADUPORT_NATIVE_REQUIRED=1 when INSTALL_NATIVE=1.
    native_required: bool = False

    # Multi-tenant API keys (P0-3). Formats:
    #   JSON file path, or inline JSON array:
    #   [{"tenant_id":"acme","api_key":"sk-…","rate_limit":120}]
    #   Or compact: "acme:sk-aaa,beta:sk-bbb"
    # When empty, falls back to single settings.api_key (tenant_id="default").
    api_keys: SecretStr = Field(default_factory=_empty_secret)

    # Enterprise license (one-machine-one-key). Off by default for OSS self-host.
    # Enterprise install.sh forces LICENSE_REQUIRED=true and generates a strong secret.
    license_required: bool = False
    license_key: SecretStr = Field(default_factory=_empty_secret)
    license_file: str = ""
    license_secret: SecretStr = Field(default_factory=_empty_secret)

    # Optional billing meter push (enterprise commercial closure). When set,
    # successful conversions POST a usage event via the IP-pinned webhook client.
    billing_webhook_url: str = ""
    billing_webhook_timeout_seconds: float = Field(default=5.0, ge=0.5, le=120.0)

    @field_validator(
        "api_key",
        "api_keys",
        "basic_auth",
        "epub_passphrase",
        "session_secret",
        "license_key",
        "license_secret",
        "webhook_secret",
        mode="before",
    )
    @classmethod
    def _coerce_secret(cls, value: object) -> SecretStr | object:
        if value is None:
            return SecretStr("")
        if isinstance(value, SecretStr):
            return value
        return SecretStr(str(value))

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
