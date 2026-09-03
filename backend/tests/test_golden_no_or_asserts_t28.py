"""T28 gate: golden tests must not use weak ``assert … or …`` forms."""

from __future__ import annotations

from pathlib import Path

_GOLDEN = Path(__file__).resolve().parent
_FILES = sorted(
    p for p in _GOLDEN.glob("test_golden*.py") if p.name != "test_golden_no_or_asserts_t28.py"
)


def test_golden_files_have_no_or_asserts():
    offenders: list[str] = []
    for path in _FILES:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if not s.startswith("assert"):
                continue
            if " or " in s:
                offenders.append(f"{path.name}:{i}: {s}")
    assert not offenders, "weak OR asserts remain in golden tests:\n" + "\n".join(offenders)
