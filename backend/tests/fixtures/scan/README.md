# Scan OCR fixtures

Image-only PDFs (Pillow raster embed) for dual-engine regression.
Each `*.expect.txt` lists substrings; OCR output should match **any** line.
Regenerate: `python scripts/gen_scan_fixtures.py`
