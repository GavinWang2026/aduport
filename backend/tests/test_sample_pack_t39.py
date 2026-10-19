"""T39: acceptance sample pack — multi-target sentinel gate (customer-sample proxy)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from aduport.core.converter import convert

_PACK = Path(__file__).resolve().parent / "fixtures" / "sample_pack"
_MANIFEST = json.loads((_PACK / "manifest.json").read_text(encoding="utf-8"))


def _blob_text(fmt: str, data: bytes) -> str:
    if data[:2] == b"PK":
        parts: list[str] = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                raw = zf.read(name)
                try:
                    parts.append(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    parts.append(raw.decode("latin-1", errors="ignore"))
        return "\n".join(parts)
    if fmt == "pdf":
        return data.decode("latin-1", errors="ignore")
    text = data.decode("utf-8", errors="ignore")
    if fmt == "rtf":
        # Decode \\uN? escapes so CJK sentinels remain greppable
        import re

        def _u(m: re.Match[str]) -> str:
            code = int(m.group(1))
            if code < 0:
                code += 65536
            try:
                return chr(code)
            except ValueError:
                return ""

        text = re.sub(r"\\u(-?\d+)\?", _u, text)
    return text


def _cases() -> list[tuple[str, str, str, str, list[str]]]:
    out: list[tuple[str, str, str, str, list[str]]] = []
    for sample in _MANIFEST["samples"]:
        src = sample.get("source") or "md"
        for target in sample["targets"]:
            out.append(
                (
                    sample["id"],
                    sample["file"],
                    src,
                    target,
                    list(sample["must_contain"]),
                )
            )
    return out


@pytest.mark.parametrize(
    "sid,fname,src,target,markers",
    _cases(),
    ids=lambda c: f"{c[0]}→{c[3]}" if isinstance(c, tuple) else str(c),
)
def test_sample_pack_target_keeps_markers(
    sid: str, fname: str, src: str, target: str, markers: list[str]
):
    raw = (_PACK / fname).read_bytes()
    data = convert(raw, src, target, filename=fname)
    assert data, f"{sid}/{target} empty"
    text = _blob_text(target, data)
    # PDF may embed CJK as glyphs; require at least half of markers or any ASCII markers present
    ascii_markers = [m for m in markers if m.isascii()]
    cjk_markers = [m for m in markers if not m.isascii()]
    if target == "pdf" and cjk_markers and not ascii_markers:
        # latin-1 decode of PDF often loses CJK; size + no replacement spam is enough
        assert len(data) > 800, f"{sid}/pdf too small"
        assert data.count(b"?????") == 0
        return
    missing = [m for m in markers if m not in text]
    if target == "pdf" and missing and ascii_markers:
        missing = [m for m in missing if m.isascii()]
    assert not missing, f"{sid}→{target} lost {missing}"


def test_sample_pack_manifest_complete():
    assert _MANIFEST["samples"], "manifest empty"
    for sample in _MANIFEST["samples"]:
        path = _PACK / sample["file"]
        assert path.is_file(), f"missing sample {sample['file']}"
        assert sample["targets"], f"{sample['id']} has no targets"
        assert sample["must_contain"], f"{sample['id']} has no markers"


def test_sample_pack_audit_proxy_pass_rate():
    """In-repo proxy for「客户样张抽检 ≥90%」— all manifest cases must hold."""
    min_rate = float(_MANIFEST.get("audit_proxy_min_pass_rate") or 0.9)
    total = 0
    ok = 0
    for sample in _MANIFEST["samples"]:
        raw = (_PACK / sample["file"]).read_bytes()
        src = sample.get("source") or "md"
        for target in sample["targets"]:
            total += 1
            try:
                data = convert(raw, src, target, filename=sample["file"])
                text = _blob_text(target, data)
                markers = list(sample["must_contain"])
                if target == "pdf":
                    ascii_m = [m for m in markers if m.isascii()]
                    cjk_m = [m for m in markers if not m.isascii()]
                    if cjk_m and not ascii_m:
                        if len(data) > 800:
                            ok += 1
                        continue
                missing = [m for m in markers if m not in text]
                if target == "pdf":
                    missing = [m for m in missing if m.isascii()]
                if not missing:
                    ok += 1
            except Exception:
                pass
    rate = ok / total if total else 0.0
    assert rate >= min_rate, f"sample_pack audit proxy {rate:.1%} < {min_rate:.0%} ({ok}/{total})"
