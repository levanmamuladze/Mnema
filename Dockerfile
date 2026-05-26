# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first so layer caches well across code edits.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

RUN mkdir -p uploads /root/.cache/huggingface

# Models are downloaded on first run into the /root/.cache/huggingface volume.
# Do NOT set TRANSFORMERS_OFFLINE/HF_HUB_OFFLINE here — that would force
# offline mode even when the volume is empty, breaking the first launch.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
