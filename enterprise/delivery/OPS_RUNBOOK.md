# AduPort Enterprise — Ops Runbook

## Daily / weekly

| Check | Command / endpoint |
|-------|-------------------|
| Liveness | `curl -sf http://127.0.0.1:8000/api/health` |
| Readiness (native) | `curl -sf http://127.0.0.1:8000/api/health/ready` |
| Doctor | `cd backend && python -m aduport.cli doctor` |
| Disk (jobs / spill) | Watch `ADUPORT_JOB_RESULT_DIR` + `/tmp/aduport-spill` |
| Auth posture | Confirm `ADUPORT_REQUIRE_AUTH=1` and `ADUPORT_SESSION_SECRET` ≥16 |

## Multi-worker

1. Set `ADUPORT_REDIS_URL` (rate limit + session revoke + license seats).
2. Set shared `ADUPORT_SESSION_SECRET` (≥16) on every replica.
3. Confirm `/api/health/detail` → `session.multi_worker_ready == true`.

## Incident: auth / cookie

- Signed cookies require strong secret; without it, API issues **opaque** sessions only (single process).
- Rotate: update `.env` → `docker compose up -d --force-recreate backend`.
- Never set `ADUPORT_REQUIRE_AUTH=0` in production private deploys (`install.sh` refuses).

## Incident: convert / native

- Check `anydoc_available` on `/api/health/detail`.
- Reinstall wheel: `bash scripts/install_native_wheel.sh --release`.
- OCR: see `enterprise/OCR.md`; integration tests are `@integration` (optional).

## Logs

- Audit: `ADUPORT_AUDIT_LOG_PATH` (JSONL).
- Compose: `docker compose logs -f backend`.

## Rollback

1. Keep previous image tag / wheel SHA256.
2. `docker compose down && docker compose up -d` with prior tag.
3. Restore `.env` from last known-good backup (secrets included).
