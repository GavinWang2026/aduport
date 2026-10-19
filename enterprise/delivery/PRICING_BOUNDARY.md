# AduPort Enterprise — Pricing Boundary

**Audience:** Private deployment buyers (law firms / government / regulated enterprises)  
**Currency:** Negotiated per contract (CNY / USD)

## Included in Enterprise private pack

| Item | Boundary |
|------|----------|
| Core conversion API + Web UI | Included |
| Multi-tenant API keys + job tokens | Included |
| HttpOnly session + `SESSION_SECRET` bootstrap | Included |
| License enforcement (one-machine-one-key) | Included |
| Offline preflight / probe scripts | Included |
| Ops Prometheus templates | Included (observability profile) |
| Prebuilt native wheels (optional) | Via Release assets / Docker `INSTALL_NATIVE=1` |

## Explicitly out of default pack

| Item | Notes |
|------|-------|
| Public SaaS multi-tenant billing portal | Optional customization |
| Pandoc / LibreOffice bundled in main image | Call-out only (`ADUPORT_LIBREOFFICE_*`) |
| GPU OCR farm / hosted model CDN | Customer-provided; see `enterprise/OCR.md` |
| Pen-test / formal certification letter | Separate SOW |

## Seat / meter hints

- Concurrent seats: license `seats` claim + optional Redis seat backend.
- Usage meter: `/api/usage` + optional `ADUPORT_BILLING_WEBHOOK_URL`.

Prices and discounts are **contract-specific**; this file defines scope, not list price.
