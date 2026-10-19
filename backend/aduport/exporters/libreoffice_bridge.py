"""Optional LibreOffice headless bridge (v8 §D) — call-out, never bundled.

Compliance: invoke system ``soffice`` / container sidecar; do **not** vendor
LibreOffice into the MIT distribution (same policy as pandoc).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from aduport.config import settings
from aduport.core.exceptions import InvalidDocumentError, UnsupportedFormatError

_log = logging.getLogger("aduport.libreoffice")


class LibreOfficeUnavailable(InvalidDocumentError):
    """Raised when LO is disabled or the binary cannot be found."""


def libreoffice_enabled() -> bool:
    return bool(getattr(settings, "libreoffice_enabled", False))


def libreoffice_bin() -> str:
    configured = (getattr(settings, "libreoffice_bin", "") or "").strip()
    if configured:
        return configured
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def convert_via_libreoffice(
    source_bytes: bytes,
    *,
    filename: str,
    target_ext: str,
    timeout: float | None = None,
) -> bytes:
    """Run ``soffice --headless --convert-to`` in a temp profile.

    Intended for pixel-ish docx/xlsx → pdf when semantic AST export is insufficient.
    """
    if not libreoffice_enabled():
        raise LibreOfficeUnavailable(
            "LibreOffice 桥未启用。设置 ADUPORT_LIBREOFFICE_ENABLED=1 并安装 soffice；"
            "AduPort 不会打包 LibreOffice。"
        )
    binary = libreoffice_bin()
    if not binary:
        raise LibreOfficeUnavailable(
            "未找到 soffice/libreoffice。请安装 LibreOffice 或设置 ADUPORT_LIBREOFFICE_BIN。"
        )
    ext = (target_ext or "").lower().lstrip(".")
    if ext not in ("pdf", "docx", "odt", "xlsx", "ods", "html", "csv"):
        raise UnsupportedFormatError(f"LibreOffice 桥不支持目标格式: {ext}")

    wait = float(timeout if timeout is not None else getattr(settings, "libreoffice_timeout_seconds", 120))
    suffix = Path(filename or "input.bin").suffix or ".bin"
    with tempfile.TemporaryDirectory(prefix="aduport-lo-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / f"input{suffix}"
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        profile = tmp_path / "profile"
        profile.mkdir()
        src.write_bytes(source_bytes)
        os.chmod(src, 0o600)
        cmd = [
            binary,
            "--headless",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            ext,
            "--outdir",
            str(out_dir),
            str(src),
        ]
        _log.info("libreoffice convert: %s → %s", filename, ext)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=max(5.0, wait),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise InvalidDocumentError(
                f"LibreOffice 转换超时（>{wait:.0f}s）: {filename}"
            ) from exc
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or b"").decode("utf-8", "ignore")[:500]
            raise InvalidDocumentError(
                f"LibreOffice 转换失败 ({proc.returncode}): {filename} — {err}"
            )
        produced = list(out_dir.glob(f"*.{ext}"))
        if not produced:
            raise InvalidDocumentError(f"LibreOffice 未产出 .{ext}: {filename}")
        return produced[0].read_bytes()
