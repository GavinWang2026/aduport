# aduport-native

Python bindings for AduPort’s vendored Rust document parser (anydoc fork).  
Distribution name: **`aduport-native`** · import: **`aduport_native._doc`**.

> Main package [`AduPort`](https://pypi.org/project/aduport/) on PyPI is **Python-only**.  
> This extension ships via **Docker**, **source compile**, or **prebuilt wheels** on GitHub Releases.

## Install (prebuilt — no Rust toolchain)

```bash
# from a Release asset (recommended)
bash scripts/install_native_wheel.sh --release

# or offline tarball / local wheel dir
bash scripts/install_native_wheel.sh --tarball aduport-native-wheels-0.2.4.tar.gz
bash scripts/install_native_wheel.sh --dir ./wheelhouse
```

CI builds portable manylinux + macOS wheels (`.github/workflows/native-wheels.yml`).

## Install (compile from this tree)

```bash
bash scripts/build_native.sh
# or: try prebuilt then compile
bash scripts/build_native.sh --prebuilt
```

Requires Rust ≥ 1.88 and `maturin>=1.9,<2`.

## Supported formats

| Format           | Extensions                                                 |
| ---------------- | ---------------------------------------------------------- |
| Word             | `.doc`, `.docx`, `.docm`                                   |
| PowerPoint       | `.ppt`, `.pps`, `.pot`, `.pptx`, `.pptm`, `.ppsx`, `.ppsm` |
| Excel            | `.xls`, `.xlsx`, `.xlsm`, `.xlsb`                          |
| OpenDocument     | `.odt`, `.ods`, `.odp`                                     |
| Rich Text Format | `.rtf`                                                     |
| EPUB             | `.epub`                                                    |
| CSV              | `.csv`                                                     |
| PDF              | `.pdf` (markdown fast path)                                |

## Usage

```python
import aduport_native._doc as anydoc

markdown = anydoc.to_markdown_bytes(data, "docx")
doc = anydoc.to_document(data, "docx")
```

Upstream engine: [Firecrawl anydoc](https://github.com/firecrawl-dev/anydoc) (MIT), vendored under `native/anydoc/`.
