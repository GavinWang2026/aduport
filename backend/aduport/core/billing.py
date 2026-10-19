"""Optional billing meter push for enterprise commercial closure (report P1-3).

OSS keeps this off. When ``ADUPORT_BILLING_WEBHOOK_URL`` is set, each successful
conversion emits a compact usage event through the IP-pinned POST helper so
downstream metering/billing can subscribe without scraping Prometheus.
"""

from __future__ import annotations

import logging
from typing import Any

from aduport.config import settings

_log = logging.getLogger("aduport.billing")


def emit_usage_event(
    *,
    source_format: str,
    target_format: str,
    bytes_in: int,
    bytes_out: int,
    tenant_id: str | None = None,
    cached: bool = False,
    importer: str = "",
) -> None:
    url = (settings.billing_webhook_url or "").strip()
    if not url:
        return
    if tenant_id is None:
        try:
            from aduport.core.request_context import current_tenant_id

            tenant_id = current_tenant_id()
        except Exception as exc:
            _log.debug("tenant_id unavailable for billing event: %s", exc)
            tenant_id = None
    payload: dict[str, Any] = {
        "event": "conversion",
        "tenant_id": tenant_id or "default",
        "source_format": source_format,
        "target_format": target_format,
        "bytes_in": int(bytes_in or 0),
        "bytes_out": int(bytes_out or 0),
        "cached": bool(cached),
        "importer": importer or "",
    }
    try:
        from aduport.core.safe_fetch import post_json_pinned

        post_json_pinned(
            url,
            payload,
            timeout=float(settings.billing_webhook_timeout_seconds or 5.0),
        )
    except Exception as exc:  # noqa: BLE001 — never fail the conversion on meter push
        _log.warning("billing webhook failed: %s", exc)
