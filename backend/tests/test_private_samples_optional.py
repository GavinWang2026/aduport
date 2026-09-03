"""Phase C · private customer sample volume (hard gate when required).

Set MDBRIDGE_PRIVATE_SAMPLES_DIR to a directory containing:
  - manifest.json  (same schema as fixtures/sample_pack/manifest.json)
  - sample files referenced by manifest

When unset:
  - default CI/dev: skip (in-repo sample_pack remains the ≥90% proxy)
  - if MDBRIDGE_PRIVATE_SAMPLES_REQUIRED=1: fail hard (enterprise / nightly gate)

CI fidelity-gates mounts fixtures/sample_pack as the private dir so the hard
gate always runs against the in-repo proxy corpus.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from app.core.converter import convert

_ENV = "MDBRIDGE_PRIVATE_SAMPLES_DIR"
_REQ = "MDBRIDGE_PRIVATE_SAMPLES_REQUIRED"


def _required() -> bool:
    return (os.environ.get(_REQ) or "").strip().lower() in ("1", "true", "yes", "on")


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
    return data.decode("utf-8", errors="ignore")


@pytest.fixture(scope="module")
def private_root() -> Path:
    raw = (os.environ.get(_ENV) or "").strip()
    if not raw:
        if _required():
            pytest.fail(
                f"{_REQ}=1 but {_ENV} unset — mount private samples or point to sample_pack"
            )
        pytest.skip(f"{_ENV} unset — use in-repo sample_pack proxy (≥90%)")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir() or not (root / "manifest.json").is_file():
        msg = f"{_ENV} set but invalid: {root}"
        if _required():
            pytest.fail(msg)
        pytest.skip(msg)
    return root


def test_private_samples_audit_hard_gate(private_root: Path):
    manifest = json.loads((private_root / "manifest.json").read_text(encoding="utf-8"))
    min_rate = float(manifest.get("audit_proxy_min_pass_rate") or 0.9)
    total = ok = 0
    for sample in manifest.get("samples") or []:
        path = private_root / sample["file"]
        if not path.is_file():
            total += 1
            continue
        raw = path.read_bytes()
        src = sample.get("source") or "md"
        for target in sample.get("targets") or []:
            total += 1
            try:
                data = convert(raw, src, target, filename=sample["file"])
                text = _blob_text(target, data)
                missing = [m for m in sample.get("must_contain") or [] if m not in text]
                if target == "pdf":
                    missing = [m for m in missing if m.isascii()]
                if not missing:
                    ok += 1
            except Exception:
                pass
    assert total > 0, "private manifest has no cases"
    rate = ok / total
    assert rate >= min_rate, f"private sample audit {rate:.1%} < {min_rate:.0%} ({ok}/{total})"


def test_private_samples_required_env_contract():
    """Documented env pair must be recognized by this module."""
    assert _ENV.startswith("MDBRIDGE_")
    assert _REQ.startswith("MDBRIDGE_")
    # Smoke: required() reads current env without raising
    assert isinstance(_required(), bool)
