"""Lightweight EPUB extract without docling — XHTML spine → HTML importer."""

from __future__ import annotations

from aduport.importers.import_guard import swallow_import_error
import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from aduport.config import settings
from aduport.core import xml_safe
from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import FileTooLargeError
from aduport.core.safe_zip import ZipInflateBudget
from aduport.importers.html_importer import import_html

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
    if not chunks:
        zf.close()
        return None

    html = "\n".join(chunks)
    bodies = re.findall(r"<body[^>]*>([\s\S]*?)</body>", html, flags=re.I)
    if bodies:
        html = "<html><body>" + "\n".join(bodies) + "</body></html>"
    model = import_html(html.encode("utf-8"), filename=(title or "epub") + ".html")
    from aduport.core.title_source import _is_fallback, _stem, resolve_title

    if title and not model.title and not _is_fallback(title, _stem(title)):
        model.title = resolve_title(title, model.nodes, filename=title)
    try:
        _hydrate_epub_images(model, zf, names, opf_dir=opf_dir)
    except Exception as exc:
        swallow_import_error(exc, where="epub_text.py:hydrate_images")
    try:
        css_blob, css_files = _collect_epub_css(
            zf, budget, names, opf=opf, opf_dir=opf_dir, spine_chunks=chunks
        )
        if css_blob:
            meta_css = dict(model.metadata or {})
            meta_css["epub_css"] = css_blob
            meta_css["epub_css_files"] = css_files[:20]
            meta_css["epub_css_chars"] = len(css_blob)
            model.metadata = meta_css
    except Exception as exc:
        swallow_import_error(exc, where="epub_text.py:collect_css")
    drm = _epub_drm_meta(
        names,
        deobfuscated=deobfuscated,
        algorithms=drm_algorithms,
    )
    if drm:
        meta = dict(model.metadata or {})
        meta.update(drm)
        model.metadata = meta
    meta = dict(model.metadata or {})
    meta.setdefault("fidelity_tier", "structured_partial")
    warns = list(meta.get("fidelity_warnings") or [])
    from aduport.core.document_model import Image

    image_count = sum(isinstance(node, Image) for node in model.nodes)
    resolved = int(meta.get("epub_images_resolved") or 0)
    if image_count and resolved < image_count:
        msg = "epub: some images unresolved"
        if msg not in warns:
            warns.append(msg)
    if not meta.get("epub_css"):
        msg = "epub: no CSS theme extracted from package"
        if msg not in warns:
            warns.append(msg)
    if warns:
        meta["fidelity_warnings"] = warns
    model.metadata = meta
    zf.close()
    return model


def _hydrate_epub_images(
    model: DocumentModel,
    zf: zipfile.ZipFile,
    names: set[str],
    *,
    opf_dir: str = "",
) -> None:
    """Resolve relative ``Image.url`` against EPUB zip members (v15 P0)."""
    from aduport.core.document_model import Image

    resolved = 0
    for node in model.nodes:
        if not isinstance(node, Image):
            continue
        if node.data:
            resolved += 1
            continue
        src = (node.url or "").strip()
        if not src or src.lower().startswith(("http://", "https://", "data:", "javascript:")):
            continue
        src = src.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        candidates = [
            src,
            (opf_dir + src).replace("\\", "/"),
            src.rsplit("/", 1)[-1],
        ]
        # normalize ./
        normed: list[str] = []
        for c in candidates:
            while "/./" in c:
                c = c.replace("/./", "/")
            normed.append(c)
        hit = None
        for c in normed:
            if c in names:
                hit = c
                break
        if hit is None:
            base = src.rsplit("/", 1)[-1]
            matches = [n for n in names if n.endswith("/" + base) or n == base]
            if matches:
                hit = matches[0]
        if hit is None:
            continue
        try:
            blob = zf.read(hit)
        except KeyError:
            continue
        if not blob:
            continue
        node.data = blob
        lower = hit.lower()
        if lower.endswith((".jpg", ".jpeg")):
            node.mime_type = "image/jpeg"
        elif lower.endswith(".gif"):
            node.mime_type = "image/gif"
        elif lower.endswith(".webp"):
            node.mime_type = "image/webp"
        elif lower.endswith(".svg"):
            node.mime_type = "image/svg+xml"
        else:
            node.mime_type = node.mime_type or "image/png"
        resolved += 1
    meta = dict(model.metadata or {})
    meta["epub_images_resolved"] = resolved
    model.metadata = meta


_CSS_MAX_CHARS = 200_000


def _collect_epub_css(
    zf: zipfile.ZipFile,
    budget: ZipInflateBudget,
    names: set[str],
    *,
    opf: ET.Element,
    opf_dir: str,
    spine_chunks: list[str],
) -> tuple[str, list[str]]:
    """Gather package + inline CSS for theme preservation (v15 §4.2).

    Returns ``(concatenated_css, source_file_list)``. Capped at ``_CSS_MAX_CHARS``.
    """
    parts: list[str] = []
    files: list[str] = []
    total = 0

    def _append(label: str, css: str) -> None:
        nonlocal total
        css = (css or "").strip()
        if not css:
            return
        room = _CSS_MAX_CHARS - total
        if room <= 0:
            return
        chunk = css if len(css) <= room else css[:room]
        parts.append(f"/* --- {label} --- */\n{chunk}")
        files.append(label)
        total += len(chunk) + len(label) + 20

    # OPF manifest items with media-type text/css
    ns = ""
    if opf.tag.startswith("{"):
        ns = opf.tag.split("}")[0] + "}"
    for item in opf.iter(f"{ns}item"):
        media = (item.get("media-type") or "").lower()
        href = (item.get("href") or "").strip()
        if not href:
            continue
        if media != "text/css" and not href.lower().endswith(".css"):
            continue
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
            raw = budget.read(zf, path).decode("utf-8", errors="replace")
        except (KeyError, FileTooLargeError):
            continue
        _append(path, raw)

    # Inline <style> and linked hrefs from spine XHTML we already decoded
    for idx, raw_html in enumerate(spine_chunks[:40]):
        for m in re.finditer(
            r"<style[^>]*>([\s\S]*?)</style>", raw_html, flags=re.I
        ):
            _append(f"spine[{idx}]:inline", m.group(1))
        for m in re.finditer(
            r'<link[^>]+rel=["\']?stylesheet["\']?[^>]*>', raw_html, flags=re.I
        ):
            tag = m.group(0)
            hm = re.search(r'href=["\']([^"\']+)["\']', tag, flags=re.I)
            if not hm:
                continue
            href = hm.group(1).strip().split("?", 1)[0].lstrip("/")
            path = opf_dir + href if not href.startswith("/") else href.lstrip("/")
            path = path.replace("\\", "/")
            if path in names and path not in files:
                try:
                    raw = budget.read(zf, path).decode("utf-8", errors="replace")
                    _append(path, raw)
                except (KeyError, FileTooLargeError):
                    continue

    return "\n\n".join(parts), files


def _open_epub_zip(data: bytes) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        pass
    from aduport.core.secrets import secret_plain

    passphrase = secret_plain(getattr(settings, "epub_passphrase", "")).strip()
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
    except Exception as exc:
        return swallow_import_error(exc, where='epub_text.py:184', default=None)


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
