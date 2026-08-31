# Stage 1: slim runtime (MD/DOCX-out/PDF-out without docling)
# Build office-capable image with: docker build --build-arg INSTALL_OFFICE=1 ...
ARG INSTALL_OFFICE=1

FROM python:3.11-slim-bookworm

ARG INSTALL_OFFICE=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

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
COPY backend/app ./app
COPY backend/main.py ./

RUN pip install --upgrade pip \
    && if [ "$INSTALL_OFFICE" = "1" ]; then pip install ".[office]"; else pip install .; fi

EXPOSE 8000

# Bind inside container; compose maps 127.0.0.1 on host
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
