"""CI ban: app/ must not call ElementTree.fromstring / ET.fromstring (use xml_safe)."""

from __future__ import annotations

import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
_FORBIDDEN = re.compile(
    r"(?:ET|ElementTree)\.fromstring\s*\(",
)


def test_no_bare_et_fromstring_in_app():
    hits: list[str] = []
    for path in sorted(_APP.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _FORBIDDEN.search(line):
                hits.append(f"{path.relative_to(_APP.parent)}:{i}: {line.strip()}")
    assert not hits, "use app.core.xml_safe.fromstring:\n" + "\n".join(hits)


def test_xml_safe_module_exists():
    from app.core import xml_safe

    assert callable(xml_safe.fromstring)
