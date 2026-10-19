"""Poll an inbox directory and convert new or updated files once."""

from __future__ import annotations

from pathlib import Path

from aduport.core.converter import convert


def scan_inbox(
    input_dir: Path | str,
    output_dir: Path | str,
    target_format: str,
    *,
    source_format: str = "",
    seen: dict[str, int] | None = None,
) -> list[str]:
    """Convert files whose mtime changed since ``seen``. Returns written paths."""
    inbox = Path(input_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    remembered = seen if seen is not None else {}
    written: list[str] = []
    want = (source_format or "").lower().lstrip(".")
    for path in sorted(p for p in inbox.iterdir() if p.is_file()):
        suffix = path.suffix.lower().lstrip(".")
        if want and suffix != want:
            continue
        stamp = path.stat().st_mtime_ns
        key = str(path.resolve())
        if remembered.get(key) == stamp:
            continue
        src = want or suffix
        data = convert(path.read_bytes(), src, target_format, filename=path.name)
        dest = out_dir / f"{path.stem}.{target_format.lower().lstrip('.')}"
        dest.write_bytes(data)
        remembered[key] = stamp
        written.append(str(dest))
    return written
