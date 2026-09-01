"""Request-scoped tenant / client context (set by auth middleware)."""

from __future__ import annotations

from contextvars import ContextVar

tenant_id_var: ContextVar[str | None] = ContextVar("mdbridge_tenant_id", default=None)
client_var: ContextVar[str] = ContextVar("mdbridge_client", default="")


def set_request_context(*, tenant_id: str | None = None, client: str = "") -> None:
    tenant_id_var.set(tenant_id)
    client_var.set(client or "")


def current_tenant_id() -> str | None:
    return tenant_id_var.get()


def current_client() -> str:
    return client_var.get() or ""
