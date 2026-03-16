# ============================================================
# Ada Platform Backend - Docker Image
# ============================================================
# Multi-stage build: slim Python image with only runtime deps.
#
# Build:  docker build -t ada-backend .
# Run:    docker run -p 8000:8000 --env-file .env ada-backend
# ============================================================

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps for pdfplumber (poppler) and python-docx (libxml2)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpoppler-cpp-dev \
        libxml2 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 ada && \
    useradd --uid 1000 --gid ada --shell /bin/bash --create-home ada

WORKDIR /app

# Install Python deps first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create runtime directories
RUN mkdir -p app/data/versions app/data/amendments app/data/audit uploads output && \
    chown -R ada:ada /app

# Switch to non-root user
USER ada

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:8000/api/health || exit 1

EXPOSE 8000

CMD ["python3", "main.py"]
