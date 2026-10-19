# AduPort Enterprise — Service Levels (Private Deploy)

**Scope:** Best-effort operational targets for customer-hosted deployments.  
**Not** a public cloud SLA; availability depends on customer infrastructure.

## Targets (private single-site)

| Metric | Target | Notes |
|--------|--------|-------|
| API liveness (`/api/health`) | 99.5% monthly | Excludes planned maintenance |
| Convert p95 (MD→MD, <1MB) | < 2s | Warm process; excludes OCR |
| Job result retention | Per `ADUPORT_JOB_TTL_SECONDS` | Default 1h metadata |
| Security patches (critical) | ≤ 14 days after fix available | Customer applies image update |

## Exclusions

- OCR / native compile / Hub model download failures outside vendor control
- Misconfiguration (`REQUIRE_AUTH=0`, missing `SESSION_SECRET` on multi-worker)
- Force majeure / customer network / storage outages

## Support channels

- Severity-1 (auth bypass / data leak): enterprise support email from purchase order
- Severity-2/3: next business day

Update this document per signed MSA when commercial terms differ.
