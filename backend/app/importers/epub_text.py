"""Lightweight EPUB extract without docling — XHTML spine → HTML importer."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core.document_model import DocumentModel
from app.importers.html_importer import import_html


def try_extract_epub_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return None
    names = set(zf.namelist())
    if "META-INF/container.xml" not in names and "mimetype" not in names:
        zf.close()
        return None

    opf_path = _find_opf(zf)
    if not opf_path:
        zf.close()
        return None

    try:
        opf = ET.fromstring(zf.read(opf_path))
    except ET.ParseError:
        zf.close()
        return None

    opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
    hrefs = _spine_hrefs(opf)
    chunks: list[str] = []
    for href in hrefs[:40]:
        path = opf_dir + href if not href.startswith("/") else href.lstrip("/")
        # normalize
        path = path.replace("\\", "/")
        while "/./" in path:
            path = path.replace("/./", "/")
        if path not in names:
            # try basename search
            base = href.rsplit("/", 1)[-1]
            matches = [n for n in names if n.endswith(base)]
            if not matches:
                continue
            path = matches[0]
        try:
            raw = zf.read(path).decode("utf-8", errors="replace")
        except KeyError:
            continue
        chunks.append(raw)
    zf.close()
    if not chunks:
        return None

    html = "\n".join(chunks)
    # Prefer body inner if present
    bodies = re.findall(r"<body[^>]*>([\s\S]*?)</body>", html, flags=re.I)
    if bodies:
        html = "<html><body>" + "\n".join(bodies) + "</body></html>"
    model = import_html(html.encode("utf-8"), filename=(title or "epub") + ".html")
    if title and not model.title:
        model.title = title
    return model


def _find_opf(zf: zipfile.ZipFile) -> str | None:
    try:
        root = ET.fromstring(zf.read("META-INF/container.xml"))
    except (KeyError, ET.ParseError):
        # fallback: first .opf
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
        # all xhtml/html in manifest
        for href in manifest.values():
            if href.endswith((".xhtml", ".html", ".htm")):
                hrefs.append(href)
    return hrefs
