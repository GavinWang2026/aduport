#!/usr/bin/env bash
# Shared: install pinned rustup + toolchain with SHA256 verify (v13 P3-2).
# Sourced/copied into Dockerfiles — do not curl|sh rustup without checksum.
#
# Override at build time:
#   --build-arg RUSTUP_VERSION=1.27.1
#   --build-arg RUST_TOOLCHAIN=1.83.0
#   --build-arg RUSTUP_INIT_SHA256=<hex>

set -euo pipefail

RUSTUP_VERSION="${RUSTUP_VERSION:-1.27.1}"
RUST_TOOLCHAIN="${RUST_TOOLCHAIN:-1.88.0}"
# Official archive checksum for x86_64-unknown-linux-gnu rustup-init 1.27.1
# https://static.rust-lang.org/rustup/archive/1.27.1/x86_64-unknown-linux-gnu/rustup-init.sha256
RUSTUP_INIT_SHA256="${RUSTUP_INIT_SHA256:-6aeece6993e902708983b209d04c0d1dbb14ebb405ddb87def578d41f920f56d}"

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) TARGET=x86_64-unknown-linux-gnu ;;
  aarch64|arm64) TARGET=aarch64-unknown-linux-gnu ;;
  *)
    echo "Unsupported arch for pinned rustup: $ARCH" >&2
    exit 1
    ;;
esac

# aarch64 checksum (archive 1.27.1) — override via RUSTUP_INIT_SHA256 if needed
if [[ "$TARGET" == "aarch64-unknown-linux-gnu" && "$RUSTUP_INIT_SHA256" == "6aeece6993e902708983b209d04c0d1dbb14ebb405ddb87def578d41f920f56d" ]]; then
  RUSTUP_INIT_SHA256="1cffbf51e63e634c746f741de50649bbbcbd9dbe1de363c9ecef64e278dba2b2"
fi

URL="https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${TARGET}/rustup-init"
echo "Fetching rustup-init ${RUSTUP_VERSION} (${TARGET})…"
curl --proto '=https' --tlsv1.2 -fsSL "$URL" -o /tmp/rustup-init
echo "${RUSTUP_INIT_SHA256}  /tmp/rustup-init" | sha256sum -c -
chmod +x /tmp/rustup-init
/tmp/rustup-init -y --default-toolchain "${RUST_TOOLCHAIN}" --profile minimal --no-modify-path
rm -f /tmp/rustup-init
echo "rustup OK → toolchain ${RUST_TOOLCHAIN}"
