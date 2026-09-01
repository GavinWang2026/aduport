"""SQLite persistence for async job metadata (survives process restart)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

_lock = threading.Lock()


def _db_path() -> Path | None:
    raw = (getattr(settings, "job_db_path", "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at REAL NOT NULL,
            tenant_id TEXT
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "tenant_id" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN tenant_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_tenant_updated "
        "ON jobs(tenant_id, updated_at DESC)"
    )
    conn.commit()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _migrate(conn)
    return conn


def _tenant_of(payload: dict[str, Any]) -> str | None:
    raw = payload.get("tenant_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def save_job_snapshot(job_id: str, payload: dict[str, Any]) -> None:
    path = _db_path()
    if path is None:
        return
    with _lock:
        conn = _connect(path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id, payload, updated_at, tenant_id) "
                "VALUES (?, ?, ?, ?)",
                (
                    job_id,
                    json.dumps(payload, ensure_ascii=False),
                    time.time(),
                    _tenant_of(payload),
                ),
            )
            conn.commit()
            # prune old
            ttl = float(settings.job_ttl_seconds)
            if ttl > 0:
                cutoff = time.time() - ttl
                conn.execute("DELETE FROM jobs WHERE updated_at < ?", (cutoff,))
                conn.commit()
            max_n = int(settings.job_max_entries)
            if max_n > 0:
                rows = conn.execute(
                    "SELECT id FROM jobs ORDER BY updated_at DESC"
                ).fetchall()
                if len(rows) > max_n:
                    drop = [r["id"] for r in rows[max_n:]]
                    conn.executemany("DELETE FROM jobs WHERE id = ?", [(i,) for i in drop])
                    conn.commit()
        finally:
            conn.close()


def load_recent_snapshots(
    limit: int = 50,
    *,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    path = _db_path()
    if path is None or not path.is_file():
        return []
    with _lock:
        conn = _connect(path)
        try:
            viewer = (tenant_id or "").strip() or None
            if viewer is None:
                rows = conn.execute(
                    "SELECT payload FROM jobs "
                    "WHERE tenant_id IS NULL OR tenant_id = '' "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (max(1, limit),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload FROM jobs WHERE tenant_id = ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (viewer, max(1, limit)),
                ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                try:
                    out.append(json.loads(row["payload"]))
                except json.JSONDecodeError:
                    continue
            return out
        finally:
            conn.close()


def load_job_snapshot(job_id: str) -> dict[str, Any] | None:
    path = _db_path()
    if path is None or not path.is_file():
        return None
    with _lock:
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT payload FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                return None
            return json.loads(row["payload"])
        except (json.JSONDecodeError, sqlite3.Error):
            return None
        finally:
            conn.close()
