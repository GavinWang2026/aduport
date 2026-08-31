"""SSRF-safe outbound HTTP fetch for remote images / resources."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.core.exceptions import InvalidDocumentError

# Private / link-local / metadata ranges
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_BYTES_DEFAULT = 5 * 1024 * 1024


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
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if any(ip in net for net in _BLOCKED_NETWORKS):
            return False
    return True


def fetch_bytes(
    url: str,
    *,
    timeout: float = 8.0,
    max_bytes: int = _MAX_BYTES_DEFAULT,
) -> tuple[bytes, str]:
    """
    Fetch URL after SSRF checks. Returns (body, content_type).
    Raises InvalidDocumentError on policy / network failure.
    """
    if not is_safe_url(url):
        raise InvalidDocumentError(f"拒绝拉取不安全 URL: {url[:120]}")

    req = Request(
        url,
        headers={"User-Agent": "md-bridge/0.7 (+local converter; remote-image)"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — guarded by is_safe_url
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
    except Exception as exc:
        raise InvalidDocumentError(f"远程资源拉取失败: {url[:80]} — {exc}") from exc
