"""Optional virus scan via ClamAV CLI (clamscan / clamdscan)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

from app.config import settings
from app.core.exceptions import InvalidDocumentError, MdBridgeError

_log = logging.getLogger("mdbridge.virus")


class VirusDetectedError(MdBridgeError):
    code = "virus_detected"
    status_code = 422


def scan_bytes(data: bytes, filename: str = "upload.bin") -> None:
    """
    When MDBRIDGE_VIRUS_SCAN=true, scan with clamscan/clamdscan if present.

    No-op when disabled. If enabled but ClamAV binary missing:
    - fail_closed=true → reject upload
    - fail_closed=false → log warning and continue
    """
    if not settings.virus_scan:
        return

    binary = shutil.which("clamdscan") or shutil.which("clamscan")
    if not binary:
        msg = "已启用病毒扫描但未找到 clamscan/clamdscan"
        if settings.virus_scan_fail_closed:
            raise InvalidDocumentError(msg)
        _log.warning(msg)
        return

    with tempfile.TemporaryDirectory(prefix="mdbridge-av-") as tmpdir:
        path = os.path.join(tmpdir, os.path.basename(filename) or "upload.bin")
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, 0o600)
        cmd = [binary, "--no-summary", path]
        if binary.endswith("clamdscan"):
            cmd.insert(1, "--fdpass")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=settings.virus_scan_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            if settings.virus_scan_fail_closed:
                raise InvalidDocumentError("病毒扫描超时") from exc
            _log.warning("virus scan timeout for %s", filename)
            return

        # ClamAV: 0 clean, 1 infected, 2 error
        if proc.returncode == 1:
            detail = (proc.stdout or proc.stderr or "").strip()[:200]
            raise VirusDetectedError(f"文件未通过病毒扫描: {filename} {detail}".strip())
        if proc.returncode not in (0,):
            msg = f"病毒扫描失败 (exit {proc.returncode}): {(proc.stderr or '')[:200]}"
            if settings.virus_scan_fail_closed:
                raise InvalidDocumentError(msg)
            _log.warning(msg)
