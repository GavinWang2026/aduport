#!/usr/bin/env python3
"""Minimal ole_cfb fuzz baseline (report §4.4).

Mutates CFB-shaped bytes and asserts the Python parser never hangs / raises
non-CfbError crashes. Optional / nightly — not part of default pytest gate.

Usage (from repo root or backend/):
  python -m scripts.fuzz_ole_cfb --iters 500
  ADUPORT_FUZZ_ITERS=2000 python backend/scripts/fuzz_ole_cfb.py

Mirrors the spirit of native/anydoc/fuzz/ (seed + mutate + bounded run).
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

# Allow `python scripts/fuzz_ole_cfb.py` from backend/
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from aduport.importers.ole_cfb import (  # noqa: E402
    CfbError,
    list_cfb_stream_names,
    read_cfb_stream,
)

_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _seed_minimal() -> bytes:
    """Tiny valid-ish header + zeros (parser should reject cleanly)."""
    h = bytearray(512)
    h[0:8] = _SIG
    h[0x1E:0x20] = (9).to_bytes(2, "little")  # sector_shift
    h[0x20:0x22] = (6).to_bytes(2, "little")  # mini_shift
    return bytes(h) + b"\x00" * 2048


def _seed_from_fixture() -> bytes | None:
    try:
        from tests._ole_fixtures import build_cfb_with_stream

        return build_cfb_with_stream("Workbook", b"BK" + b"\x00" * 100)
    except Exception:
        return None


def _mutate(data: bytes, rng: random.Random) -> bytes:
    buf = bytearray(data)
    if not buf:
        buf = bytearray(_seed_minimal())
    n = rng.randint(1, min(32, max(1, len(buf))))
    for _ in range(n):
        op = rng.randrange(4)
        if op == 0 and buf:
            i = rng.randrange(len(buf))
            buf[i] = rng.randrange(256)
        elif op == 1:
            i = rng.randrange(len(buf) + 1)
            buf[i:i] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 16)))
        elif op == 2 and len(buf) > 16:
            i = rng.randrange(len(buf) - 8)
            del buf[i : i + rng.randint(1, 8)]
        else:
            # Flip sector_shift / mini_shift region when long enough
            if len(buf) >= 0x22:
                buf[0x1E] = rng.choice([0, 1, 8, 9, 12, 15, 16, 255])
                buf[0x20] = rng.choice([0, 5, 6, 12, 13, 255])
    # Occasionally force OLE signature so deeper paths run
    if rng.random() < 0.4 and len(buf) >= 8:
        buf[0:8] = _SIG
    return bytes(buf)


def _exercise(blob: bytes) -> None:
    try:
        names = list_cfb_stream_names(blob)
        for name in (names or [])[:8]:
            read_cfb_stream(blob, name)
        if names:
            read_cfb_stream(blob, names[0])
    except CfbError:
        return
    except Exception:
        # Unexpected exception types are failures for the fuzzer.
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description="ole_cfb mutation fuzz baseline")
    ap.add_argument(
        "--iters",
        type=int,
        default=int(os.environ.get("ADUPORT_FUZZ_ITERS") or 400),
    )
    ap.add_argument("--seed", type=int, default=int(os.environ.get("ADUPORT_FUZZ_SEED") or 1))
    ap.add_argument("--timeout-sec", type=float, default=60.0)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    seeds = [_seed_minimal()]
    fx = _seed_from_fixture()
    if fx:
        seeds.append(fx)

    t0 = time.monotonic()
    for i in range(args.iters):
        if time.monotonic() - t0 > args.timeout_sec:
            print(f"ole_cfb fuzz: timeout after {i} iters", file=sys.stderr)
            return 2
        base = seeds[i % len(seeds)]
        blob = _mutate(base, rng)
        try:
            _exercise(blob)
        except Exception as exc:
            print(f"FAIL iter={i} seed={args.seed}: {type(exc).__name__}: {exc}", file=sys.stderr)
            Path("ole_cfb_fuzz_crash.bin").write_bytes(blob)
            return 1

    elapsed = time.monotonic() - t0
    print(f"ole_cfb fuzz ok iters={args.iters} seed={args.seed} elapsed={elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
