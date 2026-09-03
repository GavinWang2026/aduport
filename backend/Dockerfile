# Multi-stage: compile vendored anydoc (native/anydoc/) into mdbridge_native._doc,
# then assemble the Python runtime image. Build from repo root:
#   docker build -f backend/Dockerfile -t md-bridge-backend .
#
# Non-ASCII checkout paths (e.g. macOS U+2011 hyphen): use
#   bash scripts/docker_build_safe.sh
# which rsyncs to /tmp/md-bridge-build before docker build.
#
# Flags:
#   --build-arg INSTALL_OFFICE=1|0          docling / office import extras
#   --build-arg BAKE_DOCLING_MODELS=1|0    bake models into the image
#   --build-arg INSTALL_NATIVE=1|0         compile mdbridge_native (default on)
#   --build-arg INSTALL_OCR=1|0            pip install .[ocr-mineru] + runtime OCR default on

ARG INSTALL_OFFICE=1
ARG BAKE_DOCLING_MODELS=1
ARG INSTALL_NATIVE=1

# ---------------------------------------------------------------------------
# Stage 1 — build mdbridge_native wheel from in-tree Rust source
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS native-builder

ARG INSTALL_NATIVE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

# rustc ≥ 1.88 (anydoc edition 2024 / zip)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --default-toolchain stable --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /build
COPY native/anydoc ./native/anydoc

RUN mkdir -p /wheels \
 && if [ "$INSTALL_NATIVE" = "1" ]; then \
      pip install --no-cache-dir "maturin>=1.9,<2" \
      && cd native/anydoc/python \
      && maturin build --release \
      && cp ../target/wheels/*.whl /wheels/; \
    fi

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

ARG INSTALL_OFFICE=1
ARG BAKE_DOCLING_MODELS=1
ARG INSTALL_NATIVE=1
ARG INSTALL_OCR=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    MDBRIDGE_DOCLING_OFFLINE=true \
    MDBRIDGE_ANYDOC_ENABLED=true \
    MDBRIDGE_NATIVE_REQUIRED=${INSTALL_NATIVE} \
    MDBRIDGE_DOCLING_ARTIFACTS_PATH=/opt/docling/models \
    DOCLING_ARTIFACTS_PATH=/opt/docling/models

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/README.md ./
COPY backend/LICENSE ./
COPY backend/app ./app
COPY backend/main.py ./
COPY scripts/download_docling_models.sh /tmp/download_docling_models.sh

# Layer 1: Python deps (cached independently of model bake / native wheel)
RUN pip install --upgrade pip \
    && if [ "$INSTALL_OFFICE" = "1" ]; then pip install ".[office]"; else pip install .; fi \
    && if [ "$INSTALL_OCR" = "1" ]; then \
         pip install ".[ocr-mineru]" \
         && echo "OCR extras installed — MDBRIDGE_OCR_ENABLED=true recommended at runtime"; \
       fi

# When OCR is baked in (INSTALL_OCR=1), default-enable unless overridden at runtime.
ENV MDBRIDGE_OCR_ENABLED=${INSTALL_OCR}

# Layer 1b: install the vendored Rust extension (offline, no PyPI)
COPY --from=native-builder /wheels /wheels
RUN if [ "$INSTALL_NATIVE" = "1" ] && ls /wheels/mdbridge_native*.whl >/dev/null 2>&1; then \
      pip install --no-index --no-deps /wheels/mdbridge_native*.whl \
      && python -c "import mdbridge_native._doc as d; print('mdbridge_native._doc OK')"; \
    else \
      echo "WARN: mdbridge_native wheel not found in /wheels:"; ls -la /wheels || true; \
    fi \
 && rm -rf /wheels

# Layer 2: optional bake of docling models for offline enterprise images.
# Soft-fail: Hub/mirror/Xet outages must not block shipping the runtime image.
RUN mkdir -p /opt/docling/models \
 && if [ "$INSTALL_OFFICE" = "1" ] && [ "$BAKE_DOCLING_MODELS" = "1" ]; then \
      ( \
        HF_ENDPOINT=https://hf-mirror.com \
        HF_HUB_DISABLE_XET=1 \
        HF_HUB_ENABLE_HF_TRANSFER=0 \
        python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; \
download_models(output_dir=Path('/opt/docling/models'), progress=True, with_layout=True, with_tableformer=True, with_code_formula=False, with_picture_classifier=False, with_rapidocr=True, with_easyocr=False)" \
      ) || echo "WARN: docling model bake failed — image ships without baked models; mount /opt/docling/models at runtime"; \
    fi

EXPOSE 8000

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin mdbridge \
 && mkdir -p /opt/docling/models /tmp/mdbridge \
 && chown -R mdbridge:mdbridge /app /opt/docling /tmp/mdbridge

USER mdbridge

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=3)"

# Bind inside container; compose maps 127.0.0.1 on host
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
