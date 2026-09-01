# md-bridge

Local bidirectional document converter (PDF / DOCX / Markdown / ODT / …).

## Install

```bash
pip install md-bridge
# optional heavy deps:
pip install 'md-bridge[office]'    # docling for DOCX/PDF/PPTX rich import
pip install 'md-bridge[images]'    # Pillow image compress
pip install 'md-bridge[all]'
```

## CLI

```bash
mdbridge convert input.md -t docx -o out.docx
mdbridge batch ./in ./out -f md -t html
mdbridge serve   # FastAPI on :8000
```

## Python SDK

```python
from app.core.converter import convert

out = convert(open("a.md", "rb").read(), "md", "docx")
```

## Links

- Source / issues: see repository homepage
- License: MIT
