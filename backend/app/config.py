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

    ocr_enabled: bool = True

    # PDF import: auto | text | docling
    # auto = use embedded text when rich enough, else docling; Hub timeout → text fallback
    pdf_engine: str = "auto"
    pdf_text_min_chars: int = 80
    pdf_docling_tables: bool = True
    docling_artifacts_path: str = ""
    # Optional Hugging Face mirror (e.g. https://hf-mirror.com) for docling model download
    hf_endpoint: str = ""

    pdf_font_family: str = "Noto Sans CJK SC, Helvetica Neue, Arial, sans-serif"
    pdf_custom_css: str = ""
    # Chinese typesetting enhancements for PDF
    pdf_cjk_typeset: bool = True
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
    # Magic-bytes upload check
    magic_bytes_check: bool = True

    # PDF text-layer extraction
    pdf_text_parallel: bool = True
    pdf_text_workers: int = 4

    # Startup warmup (docling + WeasyPrint)
    warmup_on_startup: bool = True
    warmup_force: bool = False

    # Async jobs
    job_ttl_seconds: int = 3600
    job_max_entries: int = 256
    # 0 = unlimited in-flight upload budget for async jobs
    memory_budget_mb: int = 512

    # Optional virus scan (ClamAV)
    virus_scan: bool = False
    virus_scan_fail_closed: bool = False
    virus_scan_timeout_seconds: int = 60

    # Optional HTTP Basic Auth (user:pass). Empty = disabled.
    basic_auth: str = ""

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
