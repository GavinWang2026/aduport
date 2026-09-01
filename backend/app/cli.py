"""md-bridge CLI entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from app.core.converter import SUPPORTED_FORMATS, convert, convert_directory
from app.core.exceptions import MdBridgeError

app = typer.Typer(
    name="mdbridge",
    help="md-bridge: local document bidirectional converter (PDF/DOCX/Markdown).",
    add_completion=False,
)


def _detect_format(path: Path) -> str:
    from app.core.format_detect import detect_source_format

    fmt = detect_source_format(path.name)
    if fmt:
        return fmt
    # Target-side detection still uses extension in SUPPORTED_FORMATS
    ext = path.suffix.lower().lstrip(".")
    if ext in SUPPORTED_FORMATS:
        return ext
    raise typer.BadParameter(
        f"无法从扩展名 '.{ext}' 识别格式。支持: {sorted(SUPPORTED_FORMATS)}"
    )


@app.command("convert")
def convert_cmd(
    input_path: Path = typer.Argument(..., exists=True, readable=True, help="输入文件路径"),
    output_path: Path = typer.Argument(..., help="输出文件路径"),
    source_format: Optional[str] = typer.Option(None, "--from", "-f", help="源格式 (docx/pdf/md)"),
    target_format: Optional[str] = typer.Option(None, "--to", "-t", help="目标格式 (docx/pdf/md)"),
    pdf_font: Optional[str] = typer.Option(None, "--pdf-font", help="PDF 字体族 (CSS font-family)"),
    pdf_css: Optional[str] = typer.Option(None, "--pdf-css", help="PDF 额外 CSS 字符串"),
) -> None:
    """转换单个文档文件。"""
    src_fmt = (source_format or _detect_format(input_path)).lower().lstrip(".")
    tgt_fmt = (target_format or _detect_format(output_path)).lower().lstrip(".")

    try:
        source_bytes = input_path.read_bytes()
        result = convert(
            source_bytes,
            src_fmt,
            tgt_fmt,
            filename=input_path.name,
            pdf_font_family=pdf_font,
            pdf_custom_css=pdf_css,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(result)
        typer.echo(f"Converted {input_path} -> {output_path} ({src_fmt} -> {tgt_fmt})")
    except MdBridgeError as exc:
        typer.secho(f"错误 [{exc.code}]: {exc.message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.secho(f"错误: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command("batch")
def batch_cmd(
    input_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True, help="输入目录"),
    output_dir: Path = typer.Argument(..., help="输出目录"),
    source_format: str = typer.Option(..., "--from", "-f", help="源格式 (docx/pdf/md)"),
    target_format: str = typer.Option(..., "--to", "-t", help="目标格式 (docx/pdf/md)"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="递归扫描子目录"),
    pdf_font: Optional[str] = typer.Option(None, "--pdf-font", help="PDF 字体族 (CSS font-family)"),
    pdf_css: Optional[str] = typer.Option(None, "--pdf-css", help="PDF 额外 CSS 字符串"),
) -> None:
    """批量转换目录中的文档。"""
    src = source_format.lower().lstrip(".")
    tgt = target_format.lower().lstrip(".")
    if src not in SUPPORTED_FORMATS:
        typer.secho(f"不支持的源格式: {source_format}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if tgt not in SUPPORTED_FORMATS:
        typer.secho(f"不支持的目标格式: {target_format}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    try:
        result = convert_directory(
            input_dir,
            output_dir,
            src,
            tgt,
            recursive=recursive,
            pdf_font_family=pdf_font,
            pdf_custom_css=pdf_css,
        )
    except MdBridgeError as exc:
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
    if result.failed:
        raise typer.Exit(code=1)


@app.command("formats")
def formats_cmd() -> None:
    """列出支持的格式。"""
    typer.echo("Supported formats: " + ", ".join(sorted(SUPPORTED_FORMATS)))


@app.command("machine-id")
def machine_id_cmd() -> None:
    """打印本机 machine_id（企业一机一码绑定用）。"""
    from app.core.license import machine_id

    typer.echo(machine_id())


@app.command("license-issue")
def license_issue_cmd(
    machine: str = typer.Option(..., "--machine", "-m", help="客户 machine_id"),
    expires: str = typer.Option("", "--expires", "-e", help="到期日 YYYY-MM-DD，空=永久"),
    seats: int = typer.Option(1, "--seats", "-s", help="席位数"),
    customer: str = typer.Option("", "--customer", "-c", help="客户名称"),
) -> None:
    """签发企业 License Key（需与部署侧 MDBRIDGE_LICENSE_SECRET 一致）。"""
    from app.core.exceptions import MdBridgeError
    from app.core.license import issue_license

    try:
        token = issue_license(
            machine_id_value=machine,
            expires=expires,
            seats=seats,
            customer=customer,
        )
    except MdBridgeError as exc:
        typer.secho(f"错误 [{exc.code}]: {exc.message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(token)


@app.command("license-check")
def license_check_cmd(
    key: Optional[str] = typer.Option(None, "--key", "-k", help="License token；默认读环境变量"),
) -> None:
    """校验 License（本机指纹 + 签名 + 到期）。"""
    from app.config import settings
    from app.core.license import machine_id, parse_and_verify

    token = (key or settings.license_key or "").strip()
    if not token and settings.license_file:
        from pathlib import Path

        token = Path(settings.license_file).read_text(encoding="utf-8").strip()
    info = parse_and_verify(token)
    typer.echo(f"host_machine_id={machine_id()}")
    typer.echo(f"valid={info.valid} reason={info.reason}")
    typer.echo(f"customer={info.customer} expires={info.expires or '-'} seats={info.seats}")
    if not info.valid:
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
