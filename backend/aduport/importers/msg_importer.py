"""Outlook .msg importer — OLE CFB / MAPI string properties → DocumentModel.

No third-party OLE dependency: uses :mod:`app.importers.ole_cfb`. Adapted from
the MarkItDown OutlookMsgConverter approach (MIT), re-homed onto AduPort AST
via Markdown → :func:`import_md`. Attachments under ``__attach_version1.0_#*``
are expanded through the normal importer registry (images / office / text).
"""

from __future__ import annotations

import codecs
import re
import struct
from pathlib import Path

from aduport.core.document_model import DocumentModel, Heading, HorizontalRule, Paragraph, RichText
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError
from aduport.importers.import_guard import swallow_import_error
from aduport.importers.ole_cfb import (
    CfbError,
    list_attach_storage_names,
    list_cfb_stream_names,
    peek_cfb_path_size,
    read_cfb_path,
    read_cfb_stream,
)

_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

PROPERTIES_STREAM = "__properties_version1.0"
PROPERTIES_HEADER_SIZE = 32
PROPERTY_ENTRY_SIZE = 16
PT_LONG = 0x0003
PR_MESSAGE_CODEPAGE = 0x3FFD
PR_INTERNET_CPID = 0x3FDE

# Attachment property tags (hex id only)
_ATTACH_DATA_BIN = "37010102"  # PR_ATTACH_DATA_BIN (PT_BINARY)
_ATTACH_FILENAME = "3707"  # PR_ATTACH_LONG_FILENAME
_ATTACH_FILENAME_SHORT = "3704"  # PR_ATTACH_FILENAME
_ATTACH_DISPLAY = "3001"  # PR_DISPLAY_NAME
_ATTACH_MIME = "370E"  # PR_ATTACH_MIME_TAG
_ATTACH_METHOD = 0x3705  # in attachment __properties stream
_ATTACH_BY_VALUE = 1
_ATTACH_EMBEDDED_MSG = 5

_MAX_ATTACHMENTS = 20
_MAX_ATTACH_BYTES = 16 * 1024 * 1024

_CODEPAGE_CODECS = {
    708: "iso8859-6",
    20127: "ascii",
    20866: "koi8-r",
    21866: "koi8-u",
    28591: "iso8859-1",
    28592: "iso8859-2",
    65000: "utf-7",
    65001: "utf-8",
    54936: "gb18030",
    936: "gbk",
    950: "big5",
    932: "cp932",
    949: "euc_kr",
}


def _codec_name(codepage: int | None) -> str | None:
    if not codepage:
        return None
    name = _CODEPAGE_CODECS.get(codepage, f"cp{codepage}")
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


def _long_properties(data: bytes, *storage_path: str) -> dict[int, int]:
    try:
        if storage_path:
            blob = read_cfb_path(data, *storage_path, PROPERTIES_STREAM)
        else:
            blob = read_cfb_stream(data, PROPERTIES_STREAM)
    except CfbError:
        return {}
    if not blob:
        return {}
    # Attachment property streams use an 8-byte header (MS-OXMSG); message root uses 32.
    header = 8 if storage_path else PROPERTIES_HEADER_SIZE
    if len(blob) < header + PROPERTY_ENTRY_SIZE:
        header = 0
    props: dict[int, int] = {}
    offset = header
    while offset + PROPERTY_ENTRY_SIZE <= len(blob):
        entry = blob[offset : offset + PROPERTY_ENTRY_SIZE]
        offset += PROPERTY_ENTRY_SIZE
        tag, _flags, value = struct.unpack("<III", entry[:12])
        if tag & 0xFFFF == PT_LONG:
            props[tag >> 16] = value
    return props


def _decode_unicode(raw: bytes) -> str:
    try:
        return raw.decode("utf-16-le").rstrip("\x00").strip()
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="ignore").rstrip("\x00").strip()


def _decode_ansi(raw: bytes, encoding: str | None) -> str:
    if encoding:
        try:
            return raw.decode(encoding).rstrip("\x00").strip()
        except (UnicodeDecodeError, LookupError):
            pass
    for enc in ("utf-8", "gb18030", "cp1252", "latin-1"):
        try:
            return raw.decode(enc).rstrip("\x00").strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore").rstrip("\x00").strip()


def _mapi_string(
    data: bytes, tag_hex: str, encoding: str | None, *storage_path: str
) -> str | None:
    """Read PT_UNICODE (001F) or PT_STRING8 (001E) for a MAPI property tag."""
    uni = f"__substg1.0_{tag_hex}001F"
    ansi = f"__substg1.0_{tag_hex}001E"
    try:
        if storage_path:
            raw = read_cfb_path(data, *storage_path, uni)
            if raw:
                return _decode_unicode(raw) or None
            raw = read_cfb_path(data, *storage_path, ansi)
            if raw:
                return _decode_ansi(raw, encoding) or None
        else:
            raw = read_cfb_stream(data, uni)
            if raw:
                return _decode_unicode(raw) or None
            raw = read_cfb_stream(data, ansi)
            if raw:
                return _decode_ansi(raw, encoding) or None
    except CfbError:
        return None
    return None


def _looks_like_msg(data: bytes) -> bool:
    if len(data) < 512 or not data.startswith(_SIG):
        return False
    try:
        names = {n.lower() for n in list_cfb_stream_names(data)}
    except CfbError:
        return False
    return "__properties_version1.0" in names or any(
        n.startswith("__substg1.0_") for n in names
    )


def _safe_attach_name(name: str | None, index: int) -> str:
    raw = (name or f"attachment-{index}").strip() or f"attachment-{index}"
    raw = raw.replace("\\", "_").replace("/", "_").replace("\x00", "")
    return Path(raw).name[:180]


def extract_msg_attachments(
    data: bytes, *, encoding: str | None = None
) -> list[tuple[str, bytes]]:
    """Return ``(filename, bytes)`` for by-value file attachments."""
    out: list[tuple[str, bytes]] = []
    try:
        storages = list_attach_storage_names(data)
    except CfbError:
        return out

    for i, storage in enumerate(storages[:_MAX_ATTACHMENTS]):
        props = _long_properties(data, storage)
        method = props.get(_ATTACH_METHOD, _ATTACH_BY_VALUE)
        if method == _ATTACH_EMBEDDED_MSG:
            # Nested .msg: skip to avoid recursion bombs.
            continue
        # ATTACH_BY_VALUE = 1; missing/0 → still try binary stream.
        if method not in (0, _ATTACH_BY_VALUE):
            continue

        blob_name = f"__substg1.0_{_ATTACH_DATA_BIN}"
        declared = peek_cfb_path_size(data, storage, blob_name)
        if declared is not None and declared > _MAX_ATTACH_BYTES:
            continue

        blob = read_cfb_path(data, storage, blob_name)
        if not blob:
            continue
        if len(blob) > _MAX_ATTACH_BYTES:
            continue

        name = (
            _mapi_string(data, _ATTACH_FILENAME, encoding, storage)
            or _mapi_string(data, _ATTACH_FILENAME_SHORT, encoding, storage)
            or _mapi_string(data, _ATTACH_DISPLAY, encoding, storage)
        )
        mime = _mapi_string(data, _ATTACH_MIME, encoding, storage)
        fname = _safe_attach_name(name, i)
        if "." not in fname and mime:
            # Best-effort extension from MIME
            ext_map = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "application/pdf": ".pdf",
                "text/plain": ".txt",
                "text/html": ".html",
            }
            fname += ext_map.get(mime.lower().split(";")[0].strip(), "")
        out.append((fname, blob))
    return out


def _import_attachment_nodes(fname: str, blob: bytes) -> list:
    from aduport.core.format_detect import detect_source_format
    from aduport.core.registry import ensure_builtin_plugins, get_importer

    ensure_builtin_plugins()
    nodes: list = [
        Heading(level=3, text=RichText(text=fname)),
    ]
    fmt = detect_source_format(fname, blob)
    if fmt == "msg":
        nodes.append(Paragraph(text=RichText(text="(skipped nested .msg attachment)")))
        return nodes
    if fmt is None:
        nodes.append(
            Paragraph(text=RichText(text=f"(unsupported attachment type: {fname})"))
        )
        return nodes
    importer = get_importer(fmt)
    if importer is None:
        nodes.append(Paragraph(text=RichText(text=f"(no importer for {fmt})")))
        return nodes
    try:
        try:
            part = importer(blob, fname)
        except TypeError:
            part = importer(blob)
    except Exception as exc:
        swallow_import_error(exc, where="msg_importer._import_attachment_nodes")
        nodes.append(
            Paragraph(text=RichText(text=f"(attach convert failed: {type(exc).__name__})"))
        )
        return nodes
    nodes.extend(part.nodes)
    return nodes


def import_msg(source_bytes: bytes, filename: str = "input.msg") -> DocumentModel:
    """Import an Outlook .msg message into :class:`DocumentModel`."""
    if not source_bytes.startswith(_SIG):
        raise InvalidDocumentError(f"不是 Outlook .msg（OLE）文件: {filename}")
    if not _looks_like_msg(source_bytes):
        raise InvalidDocumentError(f"OLE 容器缺少 MSG/MAPI 流: {filename}")

    try:
        longs = _long_properties(source_bytes)
    except Exception as exc:
        raise CorruptedFileError(f"无法读取 MSG 属性流: {filename}") from exc

    header_enc = _codec_name(longs.get(PR_MESSAGE_CODEPAGE)) or _codec_name(
        longs.get(PR_INTERNET_CPID)
    )
    body_enc = _codec_name(longs.get(PR_INTERNET_CPID)) or header_enc

    subject = _mapi_string(source_bytes, "0037", header_enc) or ""
    sender = (
        _mapi_string(source_bytes, "0C1F", header_enc)
        or _mapi_string(source_bytes, "0042", header_enc)
        or ""
    )
    to = _mapi_string(source_bytes, "0E04", header_enc) or ""
    body = _mapi_string(source_bytes, "1000", body_enc) or ""
    html = _mapi_string(source_bytes, "1013", body_enc)

    lines = ["# Email Message", ""]
    if sender:
        lines.append(f"**From:** {sender}")
    if to:
        lines.append(f"**To:** {to}")
    if subject:
        lines.append(f"**Subject:** {subject}")
    lines.append("")
    lines.append("## Content")
    lines.append("")
    if body:
        lines.append(body)
    elif html:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        lines.append(text)
    else:
        lines.append("_(empty body)_")

    md = "\n".join(lines).strip() + "\n"
    from aduport.importers.md_importer import import_md

    model = import_md(md.encode("utf-8"))
    title = subject or Path(filename or "message").stem
    model.title = title
    model.metadata["importer"] = "msg"
    model.metadata["source_format"] = "msg"
    if sender:
        model.metadata["email_from"] = sender
    if to:
        model.metadata["email_to"] = to

    attachments = extract_msg_attachments(source_bytes, encoding=header_enc)
    model.metadata["msg_attachments"] = len(attachments)
    if attachments:
        model.nodes.append(HorizontalRule())
        model.nodes.append(Heading(level=2, text=RichText(text="Attachments")))
        for fname, blob in attachments:
            model.nodes.append(HorizontalRule())
            model.nodes.extend(_import_attachment_nodes(fname, blob))

    return model
