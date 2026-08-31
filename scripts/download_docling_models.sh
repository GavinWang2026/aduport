#!/usr/bin/env bash
# Pre-download docling models (layout / table / OCR) for offline PDF conversion.
# Usage:
#   ./scripts/download_docling_models.sh
#   HF_ENDPOINT=https://hf-mirror.com ./scripts/download_docling_models.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export HF_ENDPOINT="${MDBRIDGE_HF_ENDPOINT:-${HF_ENDPOINT:-https://hf-mirror.com}}"
echo "Using HF_ENDPOINT=$HF_ENDPOINT"

python - <<'PY'
from pathlib import Path
from docling.utils.model_downloader import download_models
from docling.datamodel.settings import settings

out = settings.cache_dir / "models"
print(f"Downloading docling models → {out}")
download_models(
    output_dir=out,
    progress=True,
    with_layout=True,
    with_tableformer=True,
    with_code_formula=False,
    with_picture_classifier=False,
    with_rapidocr=True,
    with_easyocr=False,
)
print("Done. Set MDBRIDGE_DOCLING_ARTIFACTS_PATH=", out)
print("Or leave empty — docling will use ~/.cache/docling/models")
PY
