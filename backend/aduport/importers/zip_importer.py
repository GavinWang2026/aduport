"""ZIP archive importer — safe inflate each member, convert, concatenate AST."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from aduport.core.document_model import DocumentModel, Heading, HorizontalRule, Paragraph, RichText
from aduport.core.exceptions import InvalidDocumentError, UnsupportedFormatError
from aduport.core.safe_zip import (
    assert_zip_package_budget,
    read_zip_member,
    zip_inflate_session,
)


# Formats we refuse to recurse into (zip bombs / nested archives).
_SKIP_NESTED = frozenset({"zip"})
_MAX_MEMBERS = 64


def import_zip(source_bytes: bytes, filename: str = "archive.zip") -> DocumentModel:
    """Import a ZIP by converting each supported member into one document.

    Uses :mod:`aduport.core.safe_zip` budgets. Nested ZIPs are skipped. Unsupported
    members are noted as paragraphs rather than failing the whole archive.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(source_bytes))
    except zipfile.BadZipFile as exc:
        raise InvalidDocumentError(f"损坏的 ZIP: {filename}") from exc

    with zf, zip_inflate_session():
        assert_zip_package_budget(zf)
        # Stable order; skip directories and hidden junk.
        members = [
            info
            for info in sorted(zf.infolist(), key=lambda i: i.filename)
            if not info.is_dir()
            and not info.filename.startswith("__MACOSX/")
            and "/." not in f"/{info.filename}"
            and not Path(info.filename).name.startswith(".")
        ]
        if not members:
            raise InvalidDocumentError(f"ZIP 无可用文件: {filename}")
        if len(members) > _MAX_MEMBERS:
            members = members[:_MAX_MEMBERS]

        from aduport.core.format_detect import detect_source_format
        from aduport.core.registry import ensure_builtin_plugins, get_importer

        ensure_builtin_plugins()

        nodes: list = [
            Heading(level=1, text=RichText(text=f"Archive: {Path(filename).name}")),
        ]
        converted = 0
        for info in members:
            name = info.filename
            stem = Path(name).name
            fmt = detect_source_format(stem)
            nodes.append(HorizontalRule())
            nodes.append(Heading(level=2, text=RichText(text=f"File: {name}")))

            if fmt is None:
                nodes.append(
                    Paragraph(text=RichText(text=f"(unsupported member: {stem})"))
                )
                continue
            if fmt in _SKIP_NESTED:
                nodes.append(Paragraph(text=RichText(text="(skipped nested zip)")))
                continue

            try:
                raw = read_zip_member(zf, name)
            except Exception as exc:
                nodes.append(
                    Paragraph(text=RichText(text=f"(read failed: {type(exc).__name__})"))
                )
                continue

            importer = get_importer(fmt)
            if importer is None:
                nodes.append(
                    Paragraph(text=RichText(text=f"(no importer for {fmt})"))
                )
                continue
            try:
                # Match filename-aware importers.
                try:
                    part = importer(raw, stem)
                except TypeError:
                    part = importer(raw)
            except (UnsupportedFormatError, InvalidDocumentError) as exc:
                nodes.append(
                    Paragraph(text=RichText(text=f"(convert failed: {exc})"))
                )
                continue
            except Exception as exc:
                nodes.append(
                    Paragraph(
                        text=RichText(text=f"(convert error: {type(exc).__name__})")
                    )
                )
                continue

            if part.title and not any(
                isinstance(n, Heading) and n.level == 1 for n in part.nodes[:1]
            ):
                nodes.append(Heading(level=3, text=RichText(text=part.title)))
            nodes.extend(part.nodes)
            converted += 1

        if converted == 0:
            raise InvalidDocumentError(f"ZIP 内无成功转换的成员: {filename}")

        model = DocumentModel(title=Path(filename).stem, nodes=nodes)
        model.metadata["importer"] = "zip"
        model.metadata["source_format"] = "zip"
        model.metadata["zip_members_converted"] = converted
        return model
