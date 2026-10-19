"""Threshold broad ``except Exception`` in importers (report P2-9).

Each import runs under an :class:`ImportFailureBudget`. Soft failures call
:func:`swallow_import_error` instead of bare ``except Exception: pass``;
once the budget is exhausted the import aborts with :class:`InvalidDocumentError`.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, TypeVar

from aduport.core.exceptions import InvalidDocumentError

_log = logging.getLogger("aduport.import_guard")

T = TypeVar("T")

_budget_var: ContextVar["ImportFailureBudget | None"] = ContextVar(
    "import_failure_budget", default=None
)

_DEFAULT_LIMIT = 32


class ImportFailureBudget:
    """Count soft import failures; abort when ``limit`` is reached."""

    __slots__ = ("limit", "count", "last_where")

    def __init__(self, limit: int = _DEFAULT_LIMIT) -> None:
        self.limit = max(1, int(limit))
        self.count = 0
        self.last_where = ""

    def record(self, exc: BaseException | None = None, *, where: str = "") -> None:
        self.count += 1
        if where:
            self.last_where = where
        if exc is not None and _log.isEnabledFor(logging.DEBUG):
            _log.debug("import soft-fail #%s at %s: %s", self.count, where or "?", exc)
        if self.count >= self.limit:
            detail = f"（最近: {self.last_where}）" if self.last_where else ""
            raise InvalidDocumentError(
                f"导入过程中异常过多（{self.count}/{self.limit}），已中止{detail}",
                code="import_failure_budget",
            )


def current_budget() -> ImportFailureBudget | None:
    return _budget_var.get()


def swallow_import_error(
    exc: BaseException,
    *,
    where: str = "",
    default: T | None = None,
) -> T | None:
    """Record a broad exception and return *default* (or re-raise if over budget)."""
    _log.warning(
        "swallowed %s at %s: %s",
        type(exc).__name__,
        where or "?",
        exc,
    )
    budget = _budget_var.get()
    if budget is None:
        return default
    budget.record(exc, where=where)
    return default


@contextmanager
def import_failure_budget(limit: int = _DEFAULT_LIMIT) -> Iterator[ImportFailureBudget]:
    budget = ImportFailureBudget(limit=limit)
    token = _budget_var.set(budget)
    try:
        yield budget
    finally:
        _budget_var.reset(token)
