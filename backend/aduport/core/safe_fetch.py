"""SSRF-safe outbound HTTP — unified IP-pin surface for §6.1 (P1-1).

All server-initiated egress (remote images, webhooks, billing callbacks) must
use :func:`fetch_bytes` or :func:`post_json_pinned` so DNS is resolved once and
connections are pinned to the validated address (no redirect / rebinding).
"""

from __future__ import annotations

import ipaddress
import json
import socket
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler

from aduport.core.exceptions import InvalidDocumentError

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

    Resolve DNS once, validate the IP, then connect by IP with TLS
    ``server_hostname`` set to the original host (cert check stays correct).
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
    port = parsed.port
    if port is not None and port not in (80, 443):
        raise InvalidDocumentError(f"拒绝拉取非标准端口: {url[:120]}")

    pinned_ip = _resolve_safe_ip(host, parsed.port)
    scheme = parsed.scheme.lower()
    if parsed.port:
        conn_port = parsed.port
    else:
        conn_port = 443 if scheme == "https" else 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    headers = {
        "User-Agent": "AduPort/0.7 (+local converter; remote-image)",
        "Host": host if not parsed.port else f"{host}:{parsed.port}",
    }
    try:
        status, hdrs, body = _http_pinned(
            scheme,
            pinned_ip,
            conn_port,
            host,
            method="GET",
            path=path,
            headers=headers,
            timeout=timeout,
            max_bytes=max_bytes,
        )
    except InvalidDocumentError:
        raise
    except Exception as exc:
        raise InvalidDocumentError(f"远程资源拉取失败: {url[:80]} — {exc}") from exc
    if status >= 400:
        raise InvalidDocumentError(f"远程资源拉取失败: {url[:80]} — HTTP {status}")
    ctype = (hdrs.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
    return body, ctype


def post_json_pinned(
    url: str,
    payload: dict,
    *,
    timeout: float = 10.0,
    extra_headers: dict[str, str] | None = None,
) -> int:
    """POST JSON after SSRF checks, connecting by a pinned global IP.

    HTTPS uses ``ssl.wrap_socket(..., server_hostname=<original host>)`` so
    certificate verification matches the real domain (not the literal IP).
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise InvalidDocumentError(f"拒绝回调不安全 URL: {url[:120]}") from exc
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise InvalidDocumentError(f"拒绝回调不安全 URL: {url[:120]}")
    host = parsed.hostname
    if not host or host.lower() in ("localhost", "metadata.google.internal"):
        raise InvalidDocumentError(f"拒绝回调不安全 URL: {url[:120]}")
    port = parsed.port
    if port is not None and port not in (80, 443):
        raise InvalidDocumentError(f"拒绝回调非标准端口: {url[:120]}")

    pinned_ip = _resolve_safe_ip(host, parsed.port)
    scheme = parsed.scheme.lower()
    if parsed.port:
        conn_port = parsed.port
    else:
        conn_port = 443 if scheme == "https" else 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "User-Agent": "AduPort/0.7 (+local converter; webhook)",
        "Host": host if not parsed.port else f"{host}:{parsed.port}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update({str(k): str(v) for k, v in extra_headers.items()})
    try:
        status, _hdrs, _body = _http_pinned(
            scheme,
            pinned_ip,
            conn_port,
            host,
            method="POST",
            path=path,
            headers=headers,
            body=data,
            timeout=timeout,
        )
        return int(status)
    except InvalidDocumentError:
        raise
    except Exception as exc:
        raise InvalidDocumentError(f"回调投递失败: {url[:80]} — {exc}") from exc


def _http_pinned(
    scheme: str,
    pinned_ip: str,
    port: int,
    server_hostname: str,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
    max_bytes: int | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """TCP to ``pinned_ip``; TLS SNI/cert name uses ``server_hostname``."""
    import http.client
    import ssl

    if scheme == "https":
        ctx = ssl.create_default_context()
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            pinned_ip,
            port=port,
            timeout=timeout,
            context=ctx,
        )
        # Force SNI + hostname check against the original domain, not the IP.
        conn._tunnel_host = None  # type: ignore[attr-defined]

        def _connect_https() -> None:  # noqa: ANN202
            sock = socket.create_connection((pinned_ip, port), timeout)
            conn.sock = ctx.wrap_socket(sock, server_hostname=server_hostname)

        conn.connect = _connect_https  # type: ignore[method-assign]
    else:
        conn = http.client.HTTPConnection(pinned_ip, port=port, timeout=timeout)

    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        # Refuse redirects (same policy as _NoRedirect).
        if resp.status in (301, 302, 303, 307, 308):
            loc = resp.getheader("Location") or ""
            raise InvalidDocumentError(f"拒绝跟随重定向: {loc[:120]}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise InvalidDocumentError(f"远程资源过大（>{max_bytes} bytes）")
            chunks.append(chunk)
        hdrs = {k: v for k, v in resp.getheaders()}
        return int(resp.status), hdrs, b"".join(chunks)
    finally:
        try:
            conn.close()
        except Exception:
            pass
