"""SSRF-safe outbound HTTP fetch for remote images / resources."""

from __future__ import annotations

import ipaddress
import socket
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.core.exceptions import InvalidDocumentError

# Extra explicit nets (defense in depth alongside ip.is_global)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_BYTES_DEFAULT = 5 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    """Refuse redirects so DNS-rebinding / open-redirect SSRF cannot pivot."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise InvalidDocumentError(f"拒绝跟随重定向: {newurl[:120]}")


def _normalize_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    ip = _normalize_ip(ip)
    # Whitelist-style: only globally routable addresses
    if not ip.is_global:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    host = parsed.hostname
    if not host or host.lower() in ("localhost", "metadata.google.internal"):
        return False
    port = parsed.port
    try:
        infos = socket.getaddrinfo(host, port or 80, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if _is_blocked_ip(ip):
            return False
    return True


def _resolve_safe_ip(host: str, port: int | None) -> str:
    """DNS resolve once and return a globally-routable IP, or raise."""
    try:
        infos = socket.getaddrinfo(host, port or 80, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise InvalidDocumentError(f"DNS 解析失败: {host}") from exc
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if not _is_blocked_ip(ip):
            return ip_str
    raise InvalidDocumentError(f"拒绝拉取不安全主机: {host}")


def fetch_bytes(
    url: str,
    *,
    timeout: float = 8.0,
    max_bytes: int = _MAX_BYTES_DEFAULT,
) -> tuple[bytes, str]:
    """
    Fetch URL after SSRF checks. Returns (body, content_type).

    v6/T3-#16 / D-17: resolve DNS once, validate the IP, then connect by IP
    with an explicit Host header — closes the classic rebinding TOCTOU window
    between ``is_safe_url`` and the subsequent HTTP open.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise InvalidDocumentError(f"拒绝拉取不安全 URL: {url[:120]}") from exc
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise InvalidDocumentError(f"拒绝拉取不安全 URL: {url[:120]}")
    host = parsed.hostname
    if not host or host.lower() in ("localhost", "metadata.google.internal"):
        raise InvalidDocumentError(f"拒绝拉取不安全 URL: {url[:120]}")

    pinned_ip = _resolve_safe_ip(host, parsed.port)
    # Rebuild URL with literal IP; keep Host for virtual hosts / TLS SNI via header.
    netloc = pinned_ip
    if parsed.port:
        netloc = f"{pinned_ip}:{parsed.port}"
    elif parsed.scheme.lower() == "https":
        netloc = f"{pinned_ip}:443"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    pinned_url = f"{parsed.scheme}://{netloc}{path}"

    req = Request(
        pinned_url,
        headers={
            "User-Agent": "md-bridge/0.7 (+local converter; remote-image)",
            "Host": host if not parsed.port else f"{host}:{parsed.port}",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310 — IP pinned
            ctype = (resp.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise InvalidDocumentError(f"远程资源过大（>{max_bytes} bytes）: {url[:80]}")
                chunks.append(chunk)
            return b"".join(chunks), ctype
    except InvalidDocumentError:
        raise
    except HTTPError as exc:
        raise InvalidDocumentError(f"远程资源拉取失败: {url[:80]} — HTTP {exc.code}") from exc
    except Exception as exc:
        raise InvalidDocumentError(f"远程资源拉取失败: {url[:80]} — {exc}") from exc
