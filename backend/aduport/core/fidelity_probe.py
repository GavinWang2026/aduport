"""Round-trip the open Markdown through a few targets and report AST equality."""

from __future__ import annotations

from typing import Any

_TARGETS = ("html", "docx", "xlsx", "epub", "odt", "rtf", "latex", "json")


def probe_markdown(text: str) -> dict[str, Any]:
    from aduport.core.ast_diff import diff_models
    from aduport.core.converter import _import, convert

    data = (text or "").encode("utf-8")
    left = _import(data, "md", filename="probe.md")
    rows: list[dict[str, Any]] = []
    for target in _TARGETS:
        try:
            mid = convert(data, "md", target, filename="probe.md")
            back = convert(mid, target, "md", filename=f"probe.{target}")
            right = _import(back, "md", filename="back.md")
            diff = diff_models(left, right)
            md = back.decode("utf-8", errors="replace")
            rows.append(
                {
                    "target": target,
                    "ok": True,
                    "equal": bool(diff["equal"]),
                    "changes": len(diff["changes"]),
                    "footnote_wrapped": "[[^" in md,
                    "untitled": md.lstrip().startswith("# Untitled"),
                }
            )
        except Exception as exc:  # noqa: BLE001 — one target must not hide the rest
            rows.append(
                {
                    "target": target,
                    "ok": False,
                    "equal": False,
                    "changes": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {"rows": rows}
