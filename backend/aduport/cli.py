"""AduPort CLI entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from aduport import __version__
from aduport.core.converter import (
    EXPORT_MODES,
    SUPPORTED_FORMATS,
    SUPPORTED_SOURCE_FORMATS,
    TARGET_STRUCTURE_LIMITS,
    convert,
    convert_directory,
    is_accepted_target,
)
from aduport.core.exceptions import AduPortError

app = typer.Typer(
    name="aduport",
    help="AduPort: local document bidirectional converter (PDF/DOCX/Markdown).",
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        None, "--version", "-V", help="显示版本号", callback=_print_version, is_eager=True
    ),
) -> None:
    """AduPort 主命令。"""


def _detect_format(path: Path) -> str:
    from aduport.core.format_detect import detect_source_format

    fmt = detect_source_format(path.name)
    if fmt:
        return fmt
    # Target-side detection still uses extension in SUPPORTED_FORMATS
    ext = path.suffix.lower().lstrip(".")
    if ext in {"pages", "numbers", "key", "keynote"}:
        raise typer.BadParameter(
            f"{ext} 已摘牌：真实 iWork '13+ 正文在 Snappy 压缩的 IWA 中，当前不能可靠提取"
        )
    if ext in SUPPORTED_FORMATS or ext in EXPORT_MODES:
        return ext
    raise typer.BadParameter(
        f"无法从扩展名 '.{ext}' 识别格式。支持: {sorted(SUPPORTED_FORMATS)}"
    )


def _pdf_docx_opts(
    pdf_font: Optional[str],
    pdf_css: Optional[str],
    pdf_page_size: Optional[str],
    pdf_orientation: Optional[str],
    pdf_header: Optional[str],
    pdf_footer: Optional[str],
    pdf_page_numbers: Optional[bool],
    pdf_toc: Optional[bool],
    docx_theme: Optional[str],
    rag_max_chars: Optional[int] = None,
    rag_chunk_overlap: Optional[int] = None,
    embed: Optional[bool] = None,
    force_ocr: Optional[bool] = None,
    pdf_engine: Optional[str] = None,
    cache_bypass: Optional[bool] = None,
    docx_auto_toc: Optional[bool] = None,
    latex_plain_comments: Optional[bool] = None,
    csv_table_only: Optional[bool] = None,
) -> dict:
    opts = {
        "pdf_font_family": pdf_font,
        "pdf_custom_css": pdf_css,
        "pdf_page_size": pdf_page_size,
        "pdf_orientation": pdf_orientation,
        "pdf_header": pdf_header,
        "pdf_footer": pdf_footer,
        "pdf_page_numbers": pdf_page_numbers,
        "pdf_toc": pdf_toc,
        "docx_theme": docx_theme,
    }
    if rag_max_chars is not None:
        opts["rag_max_chars"] = rag_max_chars
    if rag_chunk_overlap is not None:
        opts["rag_chunk_overlap"] = rag_chunk_overlap
    if embed is not None:
        opts["embed"] = embed
    if force_ocr is not None:
        opts["force_ocr"] = force_ocr
    if pdf_engine:
        opts["pdf_engine"] = pdf_engine
    if cache_bypass is not None:
        opts["cache_bypass"] = cache_bypass
    if docx_auto_toc is not None:
        opts["docx_auto_toc"] = docx_auto_toc
    if latex_plain_comments is not None:
        opts["latex_plain_comments"] = latex_plain_comments
    if csv_table_only is not None:
        opts["csv_table_only"] = csv_table_only
    return opts


@app.command("convert")
def convert_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True, help="输入文件路径"),
    output_path: Path = typer.Argument(..., help="输出文件路径"),
    source_format: Optional[str] = typer.Option(None, "--from", "-f", help="源格式"),
    target_format: Optional[str] = typer.Option(None, "--to", "-t", help="目标格式"),
    pdf_font: Optional[str] = typer.Option(None, "--pdf-font", help="PDF 字体族 (CSS font-family)"),
    pdf_css: Optional[str] = typer.Option(None, "--pdf-css", help="PDF 额外 CSS 字符串"),
    pdf_page_size: Optional[str] = typer.Option(None, "--pdf-page-size", help="PDF 纸张 A4/Letter/..."),
    pdf_orientation: Optional[str] = typer.Option(None, "--pdf-orientation", help="portrait|landscape"),
    pdf_header: Optional[str] = typer.Option(None, "--pdf-header", help="PDF 页眉"),
    pdf_footer: Optional[str] = typer.Option(None, "--pdf-footer", help="PDF 页脚"),
    pdf_page_numbers: Optional[bool] = typer.Option(None, "--pdf-page-numbers/--no-pdf-page-numbers"),
    pdf_toc: Optional[bool] = typer.Option(None, "--pdf-toc/--no-pdf-toc", help="PDF 目录"),
    docx_theme: Optional[str] = typer.Option(None, "--docx-theme", help="default|academic|minimal"),
    rag_max_chars: Optional[int] = typer.Option(
        None, "--rag-max-chars", help="RAG 元素最大字符数（target=rag）"
    ),
    rag_chunk_overlap: Optional[int] = typer.Option(
        None, "--rag-chunk-overlap", help="RAG 切块重叠字符数"
    ),
    embed: Optional[bool] = typer.Option(
        None, "--embed/--no-embed", help="RAG 导出时写入内置 embedding（schema 1.2）"
    ),
    force_ocr: Optional[bool] = typer.Option(
        None, "--force-ocr/--no-force-ocr", help="强制走 OCR（忽略文本层/sidecar）"
    ),
    pdf_engine: Optional[str] = typer.Option(
        None,
        "--pdf-engine",
        help="import: auto|text|docling|ocr|mineru|paddleocr_vl; export: auto|weasyprint|simple",
    ),
    cache_bypass: Optional[bool] = typer.Option(
        None, "--cache-bypass/--no-cache-bypass", help="跳过结果缓存"
    ),
    docx_auto_toc: Optional[bool] = typer.Option(
        None, "--docx-toc/--no-docx-toc", help="DOCX 导出自动目录字段"
    ),
    latex_plain_comments: Optional[bool] = typer.Option(
        None,
        "--latex-plain-comments/--no-latex-plain-comments",
        help="LaTeX 导出可检索的纯文本注释",
    ),
    csv_table_only: Optional[bool] = typer.Option(
        None,
        "--csv-table-only/--no-csv-table-only",
        help="CSV 目标只导出表格（省略 _CONTENT Markdown 段）",
    ),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的输出文件"),
) -> None:
    """转换单个文档文件。"""
    src_fmt = (source_format or _detect_format(input_path)).lower().lstrip(".")
    tgt_fmt = (target_format or _detect_format(output_path)).lower().lstrip(".")
    opts = _pdf_docx_opts(
        pdf_font, pdf_css, pdf_page_size, pdf_orientation,
        pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
        rag_max_chars, rag_chunk_overlap, embed, force_ocr, pdf_engine, cache_bypass,
        docx_auto_toc, latex_plain_comments, csv_table_only,
    )

    try:
        from aduport.config import settings

        size = input_path.stat().st_size
        if size > settings.max_upload_bytes:
            typer.secho(
                f"文件过大: {size} bytes（限制 {settings.max_upload_mb} MB）",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        if output_path.exists() and not force:
            typer.secho(
                f"输出已存在: {output_path}（使用 --force 覆盖）",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        source_bytes = input_path.read_bytes()
        result = convert(
            source_bytes,
            src_fmt,
            tgt_fmt,
            filename=input_path.name,
            **opts,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(result)
        typer.echo(f"Converted {input_path} -> {output_path} ({src_fmt} -> {tgt_fmt})")
        from aduport.exporters.export_warnings import get_last_export_warnings

        for note in get_last_export_warnings():
            typer.secho(f"warning: {note}", fg=typer.colors.YELLOW, err=True)
    except AduPortError as exc:
        typer.secho(f"错误 [{exc.code}]: {exc.message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.secho(f"错误: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command("batch")
def batch_cmd(
    input_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True, help="输入目录"),
    output_dir: Path = typer.Argument(..., help="输出目录"),
    source_format: str = typer.Option(..., "--from", "-f", help="源格式"),
    target_format: str = typer.Option(..., "--to", "-t", help="目标格式"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="递归扫描子目录"),
    pdf_font: Optional[str] = typer.Option(None, "--pdf-font", help="PDF 字体族 (CSS font-family)"),
    pdf_css: Optional[str] = typer.Option(None, "--pdf-css", help="PDF 额外 CSS 字符串"),
    pdf_page_size: Optional[str] = typer.Option(None, "--pdf-page-size", help="PDF 纸张"),
    pdf_orientation: Optional[str] = typer.Option(None, "--pdf-orientation", help="portrait|landscape"),
    pdf_header: Optional[str] = typer.Option(None, "--pdf-header", help="PDF 页眉"),
    pdf_footer: Optional[str] = typer.Option(None, "--pdf-footer", help="PDF 页脚"),
    pdf_page_numbers: Optional[bool] = typer.Option(None, "--pdf-page-numbers/--no-pdf-page-numbers"),
    pdf_toc: Optional[bool] = typer.Option(None, "--pdf-toc/--no-pdf-toc"),
    docx_theme: Optional[str] = typer.Option(None, "--docx-theme", help="default|academic|minimal"),
    rag_max_chars: Optional[int] = typer.Option(
        None, "--rag-max-chars", help="RAG 元素最大字符数（target=rag）"
    ),
    rag_chunk_overlap: Optional[int] = typer.Option(
        None, "--rag-chunk-overlap", help="RAG 切块重叠字符数"
    ),
    embed: Optional[bool] = typer.Option(None, "--embed/--no-embed"),
    force_ocr: Optional[bool] = typer.Option(None, "--force-ocr/--no-force-ocr"),
    pdf_engine: Optional[str] = typer.Option(
        None,
        "--pdf-engine",
        help="import: auto|text|docling|ocr|mineru|paddleocr_vl; export: auto|weasyprint|simple",
    ),
    cache_bypass: Optional[bool] = typer.Option(None, "--cache-bypass/--no-cache-bypass"),
    docx_auto_toc: Optional[bool] = typer.Option(None, "--docx-toc/--no-docx-toc"),
    latex_plain_comments: Optional[bool] = typer.Option(
        None, "--latex-plain-comments/--no-latex-plain-comments"
    ),
    csv_table_only: Optional[bool] = typer.Option(
        None,
        "--csv-table-only/--no-csv-table-only",
        help="CSV 目标只导出表格（省略 _CONTENT Markdown 段）",
    ),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的输出文件"),
) -> None:
    """批量转换目录中的文档。"""
    src = source_format.lower().lstrip(".")
    tgt = target_format.lower().lstrip(".")
    if src not in SUPPORTED_FORMATS:
        typer.secho(f"不支持的源格式: {source_format}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if not is_accepted_target(tgt):
        typer.secho(f"不支持的目标格式: {target_format}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    opts = _pdf_docx_opts(
        pdf_font, pdf_css, pdf_page_size, pdf_orientation,
        pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
        rag_max_chars, rag_chunk_overlap, embed, force_ocr, pdf_engine, cache_bypass,
        docx_auto_toc, latex_plain_comments, csv_table_only,
    )
    try:
        result = convert_directory(
            input_dir,
            output_dir,
            src,
            tgt,
            recursive=recursive,
            force=force,
            **opts,
        )
    except AduPortError as exc:
        typer.secho(f"错误 [{exc.code}]: {exc.message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    for item in result.items:
        if item.success:
            typer.echo(f"  OK  {item.source} -> {item.target}")
        else:
            typer.secho(
                f"  FAIL {item.source}: [{item.code}] {item.error}",
                fg=typer.colors.RED,
                err=True,
            )

    typer.echo(
        f"\n批量完成: 总计 {result.total}, 成功 {result.succeeded}, 失败 {result.failed}"
    )
    if result.degraded:
        typer.echo(f"本次 {result.degraded} 份 PDF 降级为 pdf_simple（图片/复杂表格/分页能力有限）")
    if result.failed:
        raise typer.Exit(code=1)


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址（默认仅本机）"),
    port: int = typer.Option(8000, "--port", "-p", help="监听端口"),
    reload: bool = typer.Option(False, "--reload", help="开发模式热重载"),
) -> None:
    """启动 FastAPI 文档转换服务（本地 API，浏览器访问 http://127.0.0.1:8000/docs）。"""
    import uvicorn

    typer.echo(f"AduPort API 启动: http://{host}:{port}  （Ctrl+C 停止）")
    typer.echo(_weasyprint_banner())
    uvicorn.run("main:app", host=host, port=port, reload=reload)


def _weasyprint_banner() -> str:
    try:
        import weasyprint

        version = getattr(weasyprint, "__version__", "ok")
        return f"WeasyPrint {version} — PDF 高保真可用"
    except Exception as exc:
        return (
            f"WeasyPrint 不可用（{type(exc).__name__}: {exc}）"
            " — 导出 PDF 将降级为 pdf_simple"
        )


@app.command("watch")
def watch_cmd(
    input_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    output_dir: Path = typer.Argument(...),
    target_format: str = typer.Option(..., "--to", "-t"),
    source_format: str = typer.Option("", "--from", "-f", help="只处理该后缀；空则用文件后缀"),
    interval: float = typer.Option(2.0, "--interval", help="轮询秒数"),
) -> None:
    """监视目录，把新文件或改过的文件转换到输出目录。Ctrl+C 停止。"""
    import time

    from aduport.core.watch import scan_inbox

    seen: dict[str, int] = {}
    typer.echo(f"监视 {input_dir} → {output_dir} ({target_format})，每 {interval}s")
    try:
        while True:
            written = scan_inbox(
                input_dir,
                output_dir,
                target_format,
                source_format=source_format,
                seen=seen,
            )
            for path in written:
                typer.echo(f"  OK  {path}")
            time.sleep(max(0.2, interval))
    except KeyboardInterrupt:
        typer.echo("已停止监视")


@app.command("formats")
def formats_cmd() -> None:
    """列出支持的格式。"""
    from aduport.core.converter import SUPPORTED_TARGET_FORMATS

    typer.echo(f"source ({len(SUPPORTED_SOURCE_FORMATS)}): {sorted(SUPPORTED_SOURCE_FORMATS)}")
    typer.echo(f"target ({len(SUPPORTED_TARGET_FORMATS)}): {sorted(SUPPORTED_TARGET_FORMATS)}")
    typer.echo(f"export_modes: {sorted(EXPORT_MODES)}")
    if TARGET_STRUCTURE_LIMITS:
        typer.echo("structure_limits:")
        for fmt, nodes in TARGET_STRUCTURE_LIMITS.items():
            typer.echo(f"  {fmt}: {', '.join(nodes)}")
    else:
        typer.echo("structure_limits: none")
    typer.echo("all: " + ", ".join(sorted(SUPPORTED_FORMATS)))


@app.command("machine-id")
def machine_id_cmd() -> None:
    """打印本机 machine_id（企业一机一码绑定用）。"""
    from aduport.core.license import machine_id

    typer.echo(machine_id())


@app.command("license-issue")
def license_issue_cmd(
    machine: str = typer.Option(..., "--machine", "-m", help="客户 machine_id"),
    expires: str = typer.Option("", "--expires", "-e", help="到期日 YYYY-MM-DD，空=永久"),
    seats: int = typer.Option(1, "--seats", "-s", help="席位数"),
    customer: str = typer.Option("", "--customer", "-c", help="客户名称"),
) -> None:
    """签发企业 License Key（需与部署侧 ADUPORT_LICENSE_SECRET 一致）。"""
    from aduport.core.license import issue_license

    try:
        token = issue_license(
            machine_id_value=machine,
            expires=expires,
            seats=seats,
            customer=customer,
        )
    except AduPortError as exc:
        typer.secho(f"错误 [{exc.code}]: {exc.message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(token)


@app.command("license-check")
def license_check_cmd(
    key: Optional[str] = typer.Option(None, "--key", "-k", help="License token；默认读环境变量"),
) -> None:
    """校验 License（本机指纹 + 签名 + 到期）。"""
    from aduport.config import settings
    from aduport.core.license import machine_id, parse_and_verify
    from aduport.core.secrets import secret_plain

    token = (key or secret_plain(settings.license_key) or "").strip()
    if not token and settings.license_file:
        token = Path(settings.license_file).read_text(encoding="utf-8").strip()
    info = parse_and_verify(token)
    typer.echo(f"host_machine_id={machine_id()}")
    typer.echo(f"valid={info.valid} reason={info.reason}")
    typer.echo(f"customer={info.customer} expires={info.expires or '-'} seats={info.seats}")
    if not info.valid:
        raise typer.Exit(code=1)


@app.command("doctor")
def doctor_cmd(
    json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """本地依赖自检：WeasyPrint / native / OCR / Redis / License。"""
    from aduport.config import settings
    from aduport.importers.anydoc_bridge import anydoc_available, anydoc_version
    from aduport.importers.ocr_engine import list_ocr_engines

    checks: list[dict[str, object]] = []

    def _add(name: str, ok: bool, detail: str, hint: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "hint": hint})

    # WeasyPrint / PDF export
    try:
        import weasyprint

        _add(
            "weasyprint",
            True,
            f"ok ({getattr(weasyprint, '__version__', '?')})",
        )
    except Exception as exc:
        _add(
            "weasyprint",
            False,
            f"{type(exc).__name__}: {exc}"[:160],
            "macOS: brew install pango gdk-pixbuf libffi；或用 Docker",
        )

    # Native / anydoc
    native_ok = anydoc_available()
    _add(
        "native_anydoc",
        native_ok,
        anydoc_version() if native_ok else "aduport_native 未加载",
        "" if native_ok else "预编译: scripts/install_native_wheel.sh --release；或 Docker / build_native.sh",
    )

    # OCR is ready only when an engine is actually available.
    # A disabled flag is not a pass: images then embed with no text.
    ocr_info = list_ocr_engines()
    ocr_ready = bool(isinstance(ocr_info, dict) and ocr_info.get("ready"))
    ocr_enabled = bool(settings.ocr_enabled)
    available = (ocr_info or {}).get("available") if isinstance(ocr_info, dict) else {}
    ready_names = [
        name for name, ok in (available or {}).items() if ok
    ] if isinstance(available, dict) else []
    if ocr_ready:
        checks.append(
            {
                "name": "ocr",
                "ok": True,
                "ready": True,
                "detail": "ready: " + ",".join(ready_names),
                "hint": "",
            }
        )
    else:
        if not ocr_enabled:
            detail = "not ready (ADUPORT_OCR_ENABLED=false); 未启用 OCR，仅嵌入图片"
            hint = "需要文字时再开启 OCR 并安装引擎；关闭时图片转换只嵌入原图"
        else:
            detail = "not ready (enabled but no engine available)"
            hint = "pip install 'aduport[ocr]' + 模型缓存"
        checks.append(
            {
                "name": "ocr",
                "ok": False,
                "ready": False,
                "advisory": not ocr_enabled,
                "detail": detail,
                "hint": hint,
            }
        )

    # Redis rate limit / session revoke / license seats
    redis_url = (settings.redis_url or "").strip()
    if not redis_url:
        _add(
            "redis",
            True,
            "unset (memory rate-limit / revoke / seats)",
            "多 worker 请设 ADUPORT_REDIS_URL（限流+session revoke+席位跨进程）",
        )
    else:
        try:
            import redis

            r = redis.from_url(redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
            r.ping()
            _add("redis", True, "ping ok")
        except Exception as exc:
            _add("redis", False, f"{type(exc).__name__}: {exc}"[:120], "检查 ADUPORT_REDIS_URL")

    # Session HMAC secret (multi-worker cookie sessions)
    import os

    from aduport.core.secrets import secret_plain

    sess = (os.environ.get("ADUPORT_SESSION_SECRET") or "").strip()
    if len(sess) < 16:
        sess = secret_plain(settings.license_secret).strip()
    if len(sess) < 16:
        sess = secret_plain(settings.session_secret).strip()
    if len(sess) >= 16:
        _add("session_secret", True, "HMAC secret configured (≥16)")
    else:
        _add(
            "session_secret",
            True,
            "weak/default (single-process ok)",
            "多 worker 请设 ADUPORT_SESSION_SECRET≥16 或 LICENSE_SECRET",
        )

    cookie_secure = bool(getattr(settings, "cookie_secure", True))
    _add(
        "cookie_secure",
        True,
        "on" if cookie_secure else "off (HTTP local demo)",
        "" if cookie_secure else "生产务必 ADUPORT_COOKIE_SECURE=1",
    )

    # License
    if not settings.license_required:
        _add("license", True, "not required")
    else:
        try:
            from aduport.core.license import assert_license_ok

            info = assert_license_ok()
            _add(
                "license",
                bool(info and info.valid),
                (info.reason if info else "missing") or "ok",
            )
        except AduPortError as exc:
            _add("license", False, exc.message, "配置 LICENSE_SECRET + LICENSE_KEY")

    failed = [c for c in checks if not c["ok"] and not c.get("advisory")]
    if json_out:
        import json

        ocr_check = next((c for c in checks if c["name"] == "ocr"), {})
        typer.echo(
            json.dumps(
                {
                    "ok": not failed,
                    "ocr_ready": bool(ocr_check.get("ready")),
                    "checks": checks,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        typer.echo("AduPort doctor")
        for c in checks:
            if c["ok"]:
                mark = "OK "
            elif c.get("advisory"):
                mark = "NOTE"
            else:
                mark = "FAIL"
            line = f"  [{mark}] {c['name']}: {c['detail']}"
            if c["ok"]:
                typer.secho(line, fg=typer.colors.GREEN)
            elif c.get("advisory"):
                typer.secho(line, fg=typer.colors.YELLOW)
                if c["hint"]:
                    typer.echo(f"         hint: {c['hint']}")
            else:
                typer.secho(line, fg=typer.colors.RED)
                if c["hint"]:
                    typer.echo(f"         hint: {c['hint']}")
        if failed:
            typer.secho(f"\n{len(failed)} check(s) failed", fg=typer.colors.RED, err=True)
        elif any(c.get("advisory") for c in checks):
            typer.secho("\nrequired checks passed; OCR is not ready", fg=typer.colors.YELLOW)
        else:
            typer.secho("\nall checks passed", fg=typer.colors.GREEN)

    if failed:
        raise typer.Exit(code=1)


pdf_app = typer.Typer(help="PDF 页面编排与未压缩表单（不经过 convert()）")
app.add_typer(pdf_app, name="pdf")


def _read_pdf(path: Path) -> bytes:
    data = path.read_bytes()
    if not data.startswith(b"%PDF"):
        raise typer.BadParameter(f"不是 PDF: {path}")
    return data


@pdf_app.command("merge")
def pdf_merge_cmd(
    inputs: list[Path] = typer.Argument(..., exists=True, readable=True, help="两个及以上 PDF"),
    output: Path = typer.Option(..., "--output", "-o", help="输出 PDF"),
) -> None:
    """合并多个 PDF。"""
    from aduport.core.pdf_pages import PdfPageError, merge_pdfs

    if len(inputs) < 2:
        raise typer.BadParameter("至少需要两个 PDF")
    try:
        data = merge_pdfs([_read_pdf(p) for p in inputs])
    except PdfPageError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    output.write_bytes(data)
    typer.echo(f"wrote {output} ({len(data)} bytes)")


@pdf_app.command("split")
def pdf_split_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True),
    output_dir: Path = typer.Option(Path("."), "--output-dir", "-o", help="每页一个 PDF 的目录"),
) -> None:
    """按页拆分 PDF。"""
    from aduport.core.pdf_pages import PdfPageError, split_pdf

    try:
        pieces = split_pdf(_read_pdf(input_path))
    except PdfPageError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    for i, piece in enumerate(pieces, start=1):
        dest = output_dir / f"{input_path.stem}-page-{i}.pdf"
        dest.write_bytes(piece)
        typer.echo(str(dest))


@pdf_app.command("rotate")
def pdf_rotate_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True),
    output: Path = typer.Option(..., "--output", "-o"),
    degrees: int = typer.Option(90, "--degrees", help="90 / 180 / 270"),
    pages: Optional[str] = typer.Option(None, "--pages", help="1-based，如 1,3-5；默认全部"),
) -> None:
    """旋转 PDF 页面。"""
    from aduport.core.pdf_pages import PdfPageError, rotate_pdf

    try:
        data = rotate_pdf(_read_pdf(input_path), degrees, pages)
    except PdfPageError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    output.write_bytes(data)
    typer.echo(f"wrote {output}")


@pdf_app.command("extract")
def pdf_extract_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True),
    output: Path = typer.Option(..., "--output", "-o"),
    pages: str = typer.Option(..., "--pages", help="1-based，如 1,3-5"),
) -> None:
    """抽取指定页。"""
    from aduport.core.pdf_pages import PdfPageError, extract_pages

    try:
        data = extract_pages(_read_pdf(input_path), pages)
    except PdfPageError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    output.write_bytes(data)
    typer.echo(f"wrote {output}")


@pdf_app.command("form-extract")
def pdf_form_extract_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """列出未压缩 AcroForm 字段。"""
    import json

    from aduport.core.pdf_forms import PdfFormError, extract_form_fields

    try:
        report = extract_form_fields(_read_pdf(input_path))
    except PdfFormError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@pdf_app.command("form-fill")
def pdf_form_fill_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True),
    output: Path = typer.Option(..., "--output", "-o"),
    values: str = typer.Option(..., "--values", help='JSON 对象，如 {"party":"Bob"}'),
) -> None:
    """填写未压缩 AcroForm 字段。"""
    import json

    from aduport.core.pdf_forms import PdfFormError, fill_form_fields

    try:
        parsed = json.loads(values)
    except json.JSONDecodeError as exc:
        typer.secho("values 须为 JSON 对象", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(parsed, dict):
        typer.secho("values 须为 JSON 对象", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    cleaned = {str(key): "" if val is None else str(val) for key, val in parsed.items()}
    try:
        filled = fill_form_fields(_read_pdf(input_path), cleaned)
    except PdfFormError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    output.write_bytes(filled)
    typer.echo(f"wrote {output}")


@app.command("redact")
def redact_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True, help="UTF-8 文本"),
    output: Path = typer.Option(..., "--output", "-o"),
    watermark: str = typer.Option("", "--watermark", help="文本水印"),
    pattern: Optional[str] = typer.Option(None, "--pattern", help="额外正则"),
    replacement: str = typer.Option("[REDACTED]", "--replacement"),
) -> None:
    """规则脱敏。文本可加水印；DOCX 改 run；PDF 只遮罩未压缩字面量。"""
    from aduport.core.redact import redact_docx_bytes, redact_pdf_bytes, redact_text, watermark_text

    suffix = input_path.suffix.lower()
    raw = input_path.read_bytes()
    extra = [(pattern, replacement)] if pattern else None
    if suffix == ".docx":
        try:
            out, count = redact_docx_bytes(raw, extra)
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        output.write_bytes(out)
        typer.echo(f"docx 替换 {count} 处 → {output}")
        return
    if suffix == ".pdf" or raw.startswith(b"%PDF"):
        try:
            out, count, partial = redact_pdf_bytes(raw)
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        output.write_bytes(out)
        typer.echo(f"pdf 遮罩 {count} 处 → {output}")
        if partial:
            typer.echo("压缩内容流未改写；其中的原文可能仍在")
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        typer.secho("只接受 UTF-8 文本、DOCX 或 PDF", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    try:
        redacted, count = redact_text(text, extra)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if watermark.strip():
        redacted = watermark_text(redacted, watermark)
    output.write_text(redacted, encoding="utf-8")
    typer.echo(f"wrote {output} ({count} replacements)")


@app.command("diff")
def diff_cmd(
    left: Path = typer.Argument(..., exists=True, readable=True),
    right: Path = typer.Argument(..., exists=True, readable=True),
    left_format: Optional[str] = typer.Option(None, "--left-format"),
    right_format: Optional[str] = typer.Option(None, "--right-format"),
) -> None:
    """AST 级文档比较（不是词元 Jaccard）。"""
    import json

    from aduport.core.ast_diff import diff_models
    from aduport.core.converter import _import
    from aduport.core.format_detect import detect_source_format

    lf = (left_format or detect_source_format(left.name) or "").lower()
    rf = (right_format or detect_source_format(right.name) or "").lower()
    if not lf or not rf:
        raise typer.BadParameter("无法识别格式，请传 --left-format / --right-format")
    report = diff_models(
        _import(left.read_bytes(), lf, left.name),
        _import(right.read_bytes(), rf, right.name),
    )
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["equal"]:
        raise typer.Exit(code=1)


@app.command("sign")
def sign_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True),
    output: Path = typer.Option(..., "--output", "-o", help="分离签名 JSON"),
    key: str = typer.Option(..., "--key", help="HMAC 密钥，至少 8 个字符"),
    key_id: str = typer.Option("", "--key-id", help="非密文的密钥标识"),
) -> None:
    """对文件做 HMAC-SHA256 分离签名。不改原文件，也不是 PDF 证书签名。"""
    from aduport.core.file_sign import FileSignError, dump_signature, sign_bytes

    try:
        payload = sign_bytes(input_path.read_bytes(), key, key_id)
    except FileSignError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    output.write_text(dump_signature(payload), encoding="utf-8")
    typer.echo(f"wrote {output}")


@app.command("verify")
def verify_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True),
    key: str = typer.Option(..., "--key"),
    signature: Path = typer.Option(..., "--signature", exists=True, readable=True),
) -> None:
    """校验分离签名。失败时退出码为 1。"""
    from aduport.core.file_sign import FileSignError, load_signature, verify_bytes

    try:
        payload = load_signature(signature.read_text(encoding="utf-8"))
        valid = verify_bytes(input_path.read_bytes(), key, str(payload["mac"]))
    except FileSignError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if not valid:
        typer.secho("签名不匹配", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.echo("ok")


jobs_app = typer.Typer(help="查看和续跑服务端任务")
app.add_typer(jobs_app, name="jobs")


@jobs_app.command("list")
def jobs_list_cmd(limit: int = typer.Option(20, "--limit")) -> None:
    """列出最近的转换任务。"""
    import json

    from aduport.core.ops_surface import jobs_recent

    typer.echo(json.dumps(jobs_recent(limit), ensure_ascii=False, indent=2))


@jobs_app.command("resume")
def jobs_resume_cmd(job_id: str = typer.Argument(...)) -> None:
    """把已中断、仍保留输入的任务重新排队。"""
    import json

    from aduport.core.jobs import job_store

    try:
        job = job_store.resume(job_id)
    except Exception as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(job.to_dict(include_token=False), ensure_ascii=False, indent=2))


@jobs_app.command("reprocess")
def jobs_reprocess_cmd(job_id: str = typer.Argument(...)) -> None:
    """把已完成的任务重新排队。"""
    import json

    from aduport.core.jobs import job_store

    try:
        job = job_store.reprocess(job_id)
    except Exception as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(job.to_dict(include_token=False), ensure_ascii=False, indent=2))


@app.command("fidelity")
def fidelity_cmd(text: str = typer.Option(..., "--text")) -> None:
    """对一段 Markdown 做保真度往返探针，输出 JSON。"""
    import json

    from aduport.core.fidelity_probe import probe_markdown

    typer.echo(json.dumps(probe_markdown(text), ensure_ascii=False, indent=2))


@app.command("vectors")
def vectors_cmd(
    query: str = typer.Option(..., "--query"),
    top_k: int = typer.Option(5, "--top-k"),
    collection: str = typer.Option("", "--collection"),
) -> None:
    """检索本地向量库。"""
    import json

    from aduport.config import settings
    from aduport.core.vector_store import search as vector_search

    payload = vector_search(
        query,
        collection=collection or settings.vector_collection or "default",
        top_k=max(1, min(50, top_k)),
        tenant_id="default",
    )
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("rag-feedback")
def rag_feedback_cmd(
    payload: Path = typer.Option(..., "--payload", exists=True, readable=True),
    feedback: Path = typer.Option(..., "--feedback", exists=True, readable=True),
) -> None:
    """把版面校正应用到一份 RAG JSON。"""
    import json

    from aduport.importers.layout_feedback import apply_layout_feedback_to_rag

    body = json.loads(payload.read_text(encoding="utf-8"))
    notes = json.loads(feedback.read_text(encoding="utf-8"))
    typer.echo(
        json.dumps(apply_layout_feedback_to_rag(body, notes), ensure_ascii=False, indent=2)
    )


@app.command("usage")
def usage_cmd() -> None:
    """打印今日转换计数。"""
    import json

    from aduport.core.ops_surface import usage_snapshot

    typer.echo(json.dumps(usage_snapshot(), ensure_ascii=False, indent=2))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
