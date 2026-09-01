"""Verify the anydoc-only format fix end-to-end against real anydoc fixtures."""
import sys, os, traceback

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.getcwd(), "backend"))

from app.core import converter
from app.core.registry import registered_import_formats
from app.importers.anydoc_bridge import supports, should_handle, ANYDOC_ONLY_SIBLINGS

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native", "anydoc", "tests", "fixtures")

CASES = [
    ("xlsb", "xlsb/handmade-sheet.xlsb", "md"),
    ("xlsb", "xlsb/handmade-sheet.xlsb", "csv"),   # model channel (fast only targets md)
    ("xls", "xls/sheet.xls", "md"),
    ("ods", "ods/sheet.ods", "md"),
    ("ppt", "ppt/pres.ppt", "md"),
    ("docm", None, "md"),     # no fixture; just confirm registration/no crash on unsupported path
]

print("== anydoc available:", supports("xlsb"), "| registered import formats:", len(registered_import_formats()))
print("== should_handle xlsb:", should_handle("xlsb"), "| ppt:", should_handle("ppt"),
      "| xlsx:", should_handle("xlsx"), "| docx:", should_handle("docx"))

fail = 0
for fmt, rel, tgt in CASES:
    if rel is None:
        # just confirm a registered importer exists and the wrapper is wired
        imp = converter.get_importer(fmt)
        print(f"[skip-data] {fmt} -> {tgt}: importer={imp is not None}, ANYDOC_ONLY={fmt in ANYDOC_ONLY_SIBLINGS}")
        continue
    path = os.path.join(FIX, rel)
    if not os.path.exists(path):
        print(f"[missing] fixture {path}")
        fail += 1
        continue
    data = open(path, "rb").read()
    try:
        out = converter.convert(data, fmt, tgt, filename=os.path.basename(path))
        ok = bool(out) and len(out) > 0
        head = out[:60].replace(b"\n", b" ") if isinstance(out, (bytes, bytearray)) else str(out)[:60]
        print(f"[ OK ] {fmt} -> {tgt}: {len(out)} bytes | {head!r}")
        if not ok:
            fail += 1
    except Exception as e:
        print(f"[FAIL] {fmt} -> {tgt}: {type(e).__name__}: {e}")
        traceback.print_exc()
        fail += 1

# Ensure xlsx still routes to native (not anydoc) per design intent.
print("== xlsx should_handle (native-led):", should_handle("xlsx"))
print("== anydoc-only count:", len(ANYDOC_ONLY_SIBLINGS))
print("RESULT:", "PASS" if fail == 0 else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
