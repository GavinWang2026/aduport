# AduPort Enterprise — Commercial License Boundary

**Product:** AduPort  
**Edition:** Enterprise private deployment  
**License model:** One-machine-one-key (HMAC-signed token bound to host fingerprint)  
**Status:** Binding for private installs that set `ADUPORT_LICENSE_REQUIRED=true`

## Grant

- Non-exclusive, non-transferable right to run AduPort Enterprise on **licensed seats**
  (see `seats` claim in the license token) for internal document conversion.
- Includes access to enterprise scripts under `enterprise/`, OCR runbooks, and offline pack tooling.

## Restrictions

- No redistribution of license secrets, signed tokens, or customer-specific builds as a public SaaS
  without a separate agreement.
- Main PyPI `AduPort` package is Apache-2.0 OSS and **does not** include prebuilt `aduport_native`
  wheels; native wheels ship via GitHub Releases / Docker / source build. The vendored
  `native/anydoc/` tree remains MIT (upstream firecrawl/anydoc); see `THIRD_PARTY_NOTICES.md`.
- Scanning OCR engines (MinerU / Paddle) are optional add-ons; their third-party licenses apply.

## Obligations

- Keep `ADUPORT_LICENSE_SECRET` (≥16, non-placeholder) and issued `ADUPORT_LICENSE_KEY` confidential.
- Rotate secrets via `enterprise/scripts/install.sh` when placeholders are detected.
- Do not disable `ADUPORT_REQUIRE_AUTH` in production private deploys.

## Contact

Internal commercial desk: see your purchase order / enterprise support channel.
