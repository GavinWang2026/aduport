#!/usr/bin/env bash
# =====================================================================
# sync_anydoc.sh — pull upstream Firecrawl/anydoc into the vendored tree
# ---------------------------------------------------------------------
# AduPort vendors anydoc's Rust source at native/anydoc/ (NOT an external
# PyPI dependency). Default ref is PINNED (not floating main) so abi3 /
# pymodule contracts cannot drift silently.
#
#   ./scripts/sync_anydoc.sh                      # uses UPSTREAM_PIN / default
#   ANYDOC_REF=v0.2.5 ./scripts/sync_anydoc.sh    # explicit tag/branch
#   ANYDOC_SHA=<40hex> ./scripts/sync_anydoc.sh   # exact commit (preferred)
#   ANYDOC_ALLOW_FLOAT=1 ANYDOC_REF=main ./scripts/sync_anydoc.sh
#
# What gets synced:
#   - upstream src/              -> native/anydoc/src/            (full overwrite)
#   - upstream python/src/*.rs   -> native/anydoc/python/src/     (except lib.rs)
#   - upstream fuzz/             -> native/anydoc/fuzz/           (full overwrite)
#   - upstream tests/snapshots*  -> native/anydoc/tests/          (harness + snaps)
# What is KEPT as our fork (do NOT overwrite):
#   - python/Cargo.toml         (aduport-native, path dep)
#   - python/pyproject.toml     (module-name = aduport_native._doc)
#   - python/src/lib.rs         (pymodule fn _doc + hosted-OCR stripped)
#   - python/aduport_native/   (package anchor)
#   - LICENSE                   (MIT, preserved)
#   - UPSTREAM_PIN / UPSTREAM_REV (provenance)
# After sync, rebuild: ./scripts/build_native.sh
# =====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/native/anydoc"
UPSTREAM="${ANYDOC_UPSTREAM:-https://github.com/firecrawl/anydoc.git}"
PIN_FILE="$VENDOR/UPSTREAM_PIN"
REV_FILE="$VENDOR/UPSTREAM_REV"

# Default pin: UPSTREAM_PIN file, else Cargo.toml version as vX.Y.Z, else fail.
_default_ref() {
  if [ -f "$PIN_FILE" ]; then
    # shellcheck disable=SC1090
    # Format: KEY=value lines (REF= / SHA=)
    local ref
    ref="$(grep -E '^REF=' "$PIN_FILE" | head -1 | cut -d= -f2- || true)"
    if [ -n "${ref:-}" ]; then
      echo "$ref"
      return
    fi
  fi
  local ver
  ver="$(grep -E '^version[[:space:]]*=' "$VENDOR/Cargo.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
  if [ -n "${ver:-}" ]; then
    echo "v${ver}"
    return
  fi
  echo ""
}

REF="${ANYDOC_REF:-$(_default_ref)}"
SHA="${ANYDOC_SHA:-}"
ALLOW_FLOAT="${ANYDOC_ALLOW_FLOAT:-0}"

if [ -z "$REF" ] && [ -z "$SHA" ]; then
  echo "!! No ANYDOC_REF / ANYDOC_SHA / UPSTREAM_PIN — refusing unpinned sync" >&2
  exit 1
fi

if [ "$REF" = "main" ] || [ "$REF" = "master" ]; then
  if [ "$ALLOW_FLOAT" != "1" ] && [ -z "$SHA" ]; then
    echo "!! Refusing floating '$REF'. Pin a tag (e.g. v0.2.4) or set ANYDOC_SHA." >&2
    echo "   Override only with ANYDOC_ALLOW_FLOAT=1 if you intentionally want tip." >&2
    exit 1
  fi
fi

echo "==> syncing anydoc from $UPSTREAM @ ${SHA:-$REF}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ -n "$SHA" ]; then
  git clone --filter=blob:none "$UPSTREAM" "$TMP/up" 2>&1 | tail -3
  git -C "$TMP/up" checkout --detach "$SHA"
else
  git clone --depth 1 --branch "$REF" "$UPSTREAM" "$TMP/up" 2>&1 | tail -3
fi

RESOLVED_SHA="$(git -C "$TMP/up" rev-parse HEAD)"
UPSTREAM_VER="$(grep -E '^version[[:space:]]*=' "$TMP/up/Cargo.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || true)"
LOCAL_VER="$(grep -E '^version[[:space:]]*=' "$VENDOR/Cargo.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || true)"
PY_VER="$(grep -E '^version[[:space:]]*=' "$VENDOR/python/Cargo.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || true)"

echo "  resolved SHA: $RESOLVED_SHA"
echo "  upstream crate version: ${UPSTREAM_VER:-?}"
echo "  local crate / aduport-native: ${LOCAL_VER:-?} / ${PY_VER:-?}"

if [ -n "${UPSTREAM_VER:-}" ] && [ -n "${LOCAL_VER:-}" ] && [ "$UPSTREAM_VER" != "$LOCAL_VER" ]; then
  echo "!! Version drift: upstream $UPSTREAM_VER != local Cargo.toml $LOCAL_VER" >&2
  echo "   Bump native/anydoc/Cargo.toml + python/Cargo.toml together, then rebuild." >&2
  if [ "${ANYDOC_ALLOW_VERSION_DRIFT:-0}" != "1" ]; then
    exit 1
  fi
fi

# parser core (full overwrite)
rm -rf "$VENDOR/src"
cp -R "$TMP/up/src" "$VENDOR/src"

# binding logic: overwrite document.rs etc., but KEEP our forked lib.rs
for f in "$TMP/up/python/src"/*.rs; do
  base="$(basename "$f")"
  [ "$base" = "lib.rs" ] && { echo "  skip lib.rs (local fork: module _doc + hosted-OCR stripped)"; continue; }
  cp "$f" "$VENDOR/python/src/$base"
done

cp "$TMP/up/LICENSE" "$VENDOR/LICENSE"

# Optional harness assets (fuzz + Markdown snapshot corpus). Keep local patches
# under native/anydoc/src; these dirs are additive and safe to refresh.
if [ -d "$TMP/up/fuzz" ]; then
  echo "  syncing fuzz/"
  rm -rf "$VENDOR/fuzz"
  cp -R "$TMP/up/fuzz" "$VENDOR/fuzz"
fi
if [ -d "$TMP/up/tests/snapshots" ]; then
  echo "  syncing tests/snapshots + harness"
  mkdir -p "$VENDOR/tests"
  rm -rf "$VENDOR/tests/snapshots"
  cp -R "$TMP/up/tests/snapshots" "$VENDOR/tests/snapshots"
  for f in snapshots.rs robustness.rs; do
    [ -f "$TMP/up/tests/$f" ] && cp "$TMP/up/tests/$f" "$VENDOR/tests/$f"
  done
  if [ -d "$TMP/up/tests/common" ]; then
    rm -rf "$VENDOR/tests/common"
    cp -R "$TMP/up/tests/common" "$VENDOR/tests/common"
  fi
fi
# Merge any new abuse fixtures without deleting local additions
if [ -d "$TMP/up/tests/fixtures/abuse" ]; then
  mkdir -p "$VENDOR/tests/fixtures/abuse"
  for f in "$TMP/up/tests/fixtures/abuse"/*; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    if [ ! -e "$VENDOR/tests/fixtures/abuse/$base" ]; then
      cp "$f" "$VENDOR/tests/fixtures/abuse/$base"
      echo "  + abuse fixture $base"
    fi
  done
fi

# sanity: our fork entry point must still exist
if ! grep -q "fn _doc(" "$VENDOR/python/src/lib.rs"; then
  echo "!! ERROR: python/src/lib.rs no longer defines 'fn _doc'. Re-apply the local fork." >&2
  exit 1
fi
if grep -qE 'ocr\s*=\s*"hosted"|hosted.?ocr' "$VENDOR/python/src/lib.rs" 2>/dev/null; then
  echo "!! ERROR: hosted OCR symbols reappeared in forked lib.rs — strip before commit." >&2
  exit 1
fi

# Persist pin + provenance
{
  echo "REF=${REF:-sha}"
  echo "SHA=$RESOLVED_SHA"
  echo "UPSTREAM_VERSION=${UPSTREAM_VER:-}"
  echo "SYNCED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "UPSTREAM=$UPSTREAM"
} > "$REV_FILE"

# Keep UPSTREAM_PIN as the operator-facing default (tag preferred over SHA)
if [ -z "$SHA" ]; then
  {
    echo "# Default sync target for scripts/sync_anydoc.sh (do not float on main)."
    echo "REF=$REF"
    echo "# Optional exact commit (overrides REF when set in env as ANYDOC_SHA):"
    echo "# SHA=$RESOLVED_SHA"
  } > "$PIN_FILE"
fi

echo "==> wrote $REV_FILE"
echo "==> synced. Review the diff, then: ./scripts/build_native.sh"
echo "    Contract check runs inside build_native (abi3 wheel + export symbols)."
