# AduPort Enterprise — Offline Pack

Assemble an air-gapped install bundle from a networked build host.

## Contents (recommended)

```
offline-pack/
  AduPort-src/           # git archive or release tarball
  wheels/                  # aduport_native-*.whl (+ SHA256SUMS)
  pip-cache/               # optional: pip download deps
  enterprise/              # scripts + .env.example
  CHECKSUMS.txt
```

## Build steps (networked host)

```bash
git archive --format=tar.gz -o offline-pack/AduPort-src.tgz HEAD
bash scripts/pack_native_wheels.sh
python scripts/audit_native_wheels.py wheels/
pip download -d offline-pack/pip-cache "AduPort==<version>"
(cd offline-pack && find . -type f | sort | xargs sha256sum > CHECKSUMS.txt)
```

## Air-gap install

```bash
./enterprise/scripts/offline_preflight.sh
./enterprise/scripts/install.sh
```

See also: `enterprise/scripts/pack.sh`, `scripts/install_native_wheel.sh`.
