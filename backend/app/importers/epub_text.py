"""Lightweight EPUB extract without docling — XHTML spine → HTML importer."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.config import settings
from app.core import xml_safe
from app.core.document_model import DocumentModel
from app.core.exceptions import FileTooLargeError
from app.core.safe_zip import ZipInflateBudget
from app.importers.html_importer import import_html

_IDPF_FONT_NS = "http://www.idpf.org/2008/embedding"


def try_extract_epub_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    zf = _open_epub_zip(data)
    if zf is None:
        return None
    names = set(zf.namelist())
    if "META-INF/container.xml" not in names and "mimetype" not in names:
        zf.close()
        return None

    budget = ZipInflateBudget()
    opf_path = _find_opf(zf, budget)
    if not opf_path:
        zf.close()
        return None

    try:
        opf = xml_safe.fromstring(budget.read(zf, opf_path))
    except (ET.ParseError, FileTooLargeError, KeyError):
        zf.close()
        return None

    opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
    hrefs = _spine_hrefs(opf)
    epub_id = _epub_identifier(opf)
    encrypted_paths = _encrypted_resource_paths(zf, budget)
    drm_algorithms = list(encrypted_paths.values())
    deobfuscated: list[str] = []

    chunks: list[str] = []
    for href in hrefs[:40]:
        path = opf_dir + href if not href.startswith("/") else href.lstrip("/")
        path = path.replace("\\", "/")
        while "/./" in path:
            path = path.replace("/./", "/")
        if path not in names:
            base = href.rsplit("/", 1)[-1]
            matches = [n for n in names if n.endswith(base)]
            if not matches:
                continue
            path = matches[0]
        try:
            raw_bytes = budget.read(zf, path)
        except (KeyError, FileTooLargeError):
            continue
        if path in encrypted_paths:
            deob = _try_deobfuscate(raw_bytes, epub_id, encrypted_paths[path])
            if deob is not None:
                raw_bytes = deob
                deobfuscated.append(path)
            else:
                continue
        raw = raw_bytes.decode("utf-8", errors="replace")
        chunks.append(raw)
    zf.close()
    if not chunks:
        return None

    html = "\n".join(chunks)
    bodies = re.findall(r"<body[^>]*>([\s\S]*?)</body>", html, flags=re.I)
    if bodies:
        html = "<html><body>" + "\n".join(bodies) + "</body></html>"
    model = import_html(html.encode("utf-8"), filename=(title or "epub") + ".html")
    if title and not model.title:
        model.title = title
    drm = _epub_drm_meta(
        names,
        deobfuscated=deobfuscated,
        algorithms=drm_algorithms,
    )
    if drm:
        meta = dict(model.metadata or {})
        meta.update(drm)
        model.metadata = meta
    return model


def _open_epub_zip(data: bytes) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        pass
    passphrase = (getattr(settings, "epub_passphrase", "") or "").strip()
    if not passphrase:
        return None
    try:
        return zipfile.ZipFile(BytesIO(data), "r")
    except zipfile.BadZipFile:
        return None


def _epub_drm_meta(names: set[str], *, deobfuscated: list[str] = [], algorithms: list[str] = []) -> dict[str, object]:
    if "META-INF/encryption.xml" in names:
        note = "EPUB encryption.xml 已探测"
        base: dict[str, object] = {"drm_algorithms": algorithms[:10]}
        if deobfuscated:
            return {
                "drm_protected": True,
                "drm_decrypted": "partial",
                "drm_note": note + f"；IDPF 字体去混淆 {len(deobfuscated)} 项",
                "drm_deobfuscated": deobfuscated[:20],
                **base,
            }
        return {
            "drm_protected": True,
            "drm_decrypted": False,
            "drm_policy": "adobe_lcp_requires_license_key",
            "drm_capabilities": [
                "encryption_detection",
                "idpf_font_deobfuscation",
                "spine_partial_read",
            ],
            "drm_note": note + " — Adobe/LCP 无密钥不解密；未加密 spine 仍可读",
            **base,
        }
    if any("rights" in n.lower() for n in names):
        return {"drm_protected": True, "drm_note": "EPUB 含 rights 元数据 — DRM 边界"}
    return {}


def _encrypted_resource_paths(
    zf: zipfile.ZipFile, budget: ZipInflateBudget
) -> dict[str, str]:
    """Map encrypted resource URI → algorithm URI from encryption.xml."""
    try:
        root = xml_safe.fromstring(budget.read(zf, "META-INF/encryption.xml"))
    except (KeyError, ET.ParseError, FileTooLargeError):
        return {}
    out: dict[str, str] = {}
    for data_ref in root.iter():
        if not data_ref.tag.endswith("EncryptedData"):
            continue
        uri = ""
        algo = ""
        for child in data_ref.iter():
            if child.tag.endswith("CipherReference"):
                uri = child.get("URI") or ""
            if child.tag.endswith("EncryptionMethod"):
                algo = child.get("Algorithm") or ""
        if uri:
            path = uri.lstrip("/")
            out[path] = algo
    return out


def _try_deobfuscate(data: bytes, epub_id: str, algorithm: str) -> bytes | None:
    """Reverse IDPF font obfuscation (not Adobe DRM).

    IDPF key = SHA-1(unique-identifier) → 20 bytes; XOR first min(1040, len) bytes
    with key[i % 20]. See EPUB Open Container Format §3.2.
    """
    if not epub_id or not algorithm:
        return None
    if algorithm != _IDPF_FONT_NS and "idpf.org" not in algorithm:
        return None
    if len(data) < 20:
        return None
    try:
        import hashlib

        key = hashlib.sha1(epub_id.encode("utf-8")).digest()  # noqa: S324 — IDPF mandated
        out = bytearray(data)
        for i in range(min(1040, len(out))):
            out[i] ^= key[i % 20]
        return bytes(out)
    except Exception:
        return None


def _epub_identifier(opf: ET.Element) -> str:
    ns = ""
    if opf.tag.startswith("{"):
        ns = opf.tag.split("}")[0] + "}"
    for el in opf.iter(f"{ns}identifier"):
        if el.text and el.text.strip():
            return el.text.strip()
    for el in opf.iter():
        if el.tag.endswith("}identifier") and el.text:
            return el.text.strip()
    return ""


def _find_opf(zf: zipfile.ZipFile, budget: ZipInflateBudget) -> str | None:
    try:
        root = xml_safe.fromstring(budget.read(zf, "META-INF/container.xml"))
    except (KeyError, ET.ParseError, FileTooLargeError):
        for n in zf.namelist():
            if n.endswith(".opf"):
                return n
        return None
    for el in root.iter():
        if el.tag.endswith("rootfile"):
            path = el.get("full-path")
            if path:
                return path
    return None


def _spine_hrefs(opf: ET.Element) -> list[str]:
    ns = ""
    if opf.tag.startswith("{"):
        ns = opf.tag.split("}")[0] + "}"
    manifest = {}
    for item in opf.iter(f"{ns}item"):
        iid = item.get("id")
        href = item.get("href")
        if iid and href:
            manifest[iid] = href
    hrefs: list[str] = []
    for itemref in opf.iter(f"{ns}itemref"):
        idref = itemref.get("idref")
        if idref and idref in manifest:
            hrefs.append(manifest[idref])
    if not hrefs:
        for href in manifest.values():
            if href.endswith((".xhtml", ".html", ".htm")):
                hrefs.append(href)
    return hrefs
