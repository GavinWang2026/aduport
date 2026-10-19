# AduPort

Local bidirectional document converter (PDF / DOCX / Markdown / ODT / …).

## Install

```bash
pip install aduport
# optional heavy deps:
pip install 'aduport[office]'    # docling for DOCX/PDF/PPTX rich import
pip install 'aduport[images]'    # Pillow image compress
pip install 'aduport[all]'
```

### High-fidelity Office import (anydoc / `aduport_native`)

The PyPI wheel is **Python-only** and does **not** ship the Rust extension.
Enterprise / Docker images compile it in multi-stage builds (`INSTALL_NATIVE=1`).
From a source checkout:

```bash
# requires Rust + maturin (script can pip-install maturin)
bash scripts/build_native.sh
```

Without the extension, anydoc-only formats (`xlsb`, `ods`, binary `ppt`, …) raise
`unsupported_format`; shared formats keep using native importers.

## CLI

```bash
aduport convert input.md -t docx -o out.docx
aduport batch ./in ./out -f md -t html
aduport serve   # FastAPI on :8000
```

## Python SDK

```python
from aduport.core.converter import convert

out = convert(open("a.md", "rb").read(), "md", "docx")
```

## Links

- Source / issues: see repository homepage
- License: MIT
