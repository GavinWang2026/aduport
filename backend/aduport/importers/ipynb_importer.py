"""Jupyter Notebook (.ipynb) importer → DocumentModel via Markdown."""

from __future__ import annotations

import json
from pathlib import Path

from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError
from aduport.importers.md_importer import import_md


def _cell_source(cell: dict) -> str:
    src = cell.get("source", "")
    if isinstance(src, list):
        return "".join(str(x) for x in src)
    return str(src or "")


def notebook_to_markdown(notebook: dict) -> tuple[str, str | None]:
    """Return (markdown, title) from nbformat JSON."""
    parts: list[str] = []
    title: str | None = None
    meta_title = (notebook.get("metadata") or {}).get("title")
    if isinstance(meta_title, str) and meta_title.strip():
        title = meta_title.strip()

    for cell in notebook.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        kind = cell.get("cell_type") or ""
        source = _cell_source(cell)
        if kind == "markdown":
            parts.append(source)
            if title is None:
                for line in source.splitlines():
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
        elif kind == "code":
            lang = "python"
            meta = cell.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("language"):
                lang = str(meta["language"])
            parts.append(f"```{lang}\n{source.rstrip()}\n```")
        elif kind == "raw":
            parts.append(f"```\n{source.rstrip()}\n```")

    return "\n\n".join(p for p in parts if p.strip()).strip() + "\n", title


def import_ipynb(source_bytes: bytes, filename: str = "notebook.ipynb") -> DocumentModel:
    """Import a Jupyter notebook into :class:`DocumentModel`."""
    _MAX_IPYNB_BYTES = 50 * 1024 * 1024
    _MAX_JSON_DEPTH = 64
    if len(source_bytes) > _MAX_IPYNB_BYTES:
        raise InvalidDocumentError(
            f"ipynb 过大（>{_MAX_IPYNB_BYTES} bytes）: {filename}",
            code="ipynb_too_large",
        )
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError(f"ipynb 不是合法 UTF-8: {filename}") from exc
    try:
        notebook = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptedFileError(f"ipynb JSON 损坏: {filename} — {exc}") from exc
    except RecursionError as exc:
        raise InvalidDocumentError(
            f"ipynb JSON 嵌套过深: {filename}",
            code="ipynb_too_deep",
        ) from exc

    def _check_depth(obj: object, depth: int = 0) -> None:
        if depth > _MAX_JSON_DEPTH:
            raise InvalidDocumentError(
                f"ipynb JSON 嵌套过深（>{_MAX_JSON_DEPTH}）: {filename}",
                code="ipynb_too_deep",
            )
        if isinstance(obj, dict):
            for v in obj.values():
                _check_depth(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                _check_depth(v, depth + 1)

    _check_depth(notebook)
    if not isinstance(notebook, dict):
        raise InvalidDocumentError(f"ipynb 根节点必须是对象: {filename}")
    if "nbformat" not in notebook and "cells" not in notebook:
        raise InvalidDocumentError(f"不是 Jupyter notebook（缺 nbformat/cells）: {filename}")

    md, title = notebook_to_markdown(notebook)
    model = import_md(md.encode("utf-8"))
    model.title = title or Path(filename or "notebook").stem
    model.metadata["importer"] = "ipynb"
    model.metadata["source_format"] = "ipynb"
    model.metadata["nbformat"] = notebook.get("nbformat")
    return model
