# Multi-Platform Zero-Cost Cloud Deployment Dockerfile
# Compatible with Hugging Face Spaces (UID 1000 / Port 7860) and Render ($PORT)

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (UID 1000 required for Hugging Face Spaces)
RUN useradd -m -u 1000 user

# Install Python requirements
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy application source code
COPY --chown=user:user . /app

# Ensure SQLite data directory has least-privilege ownership and permissions for WAL mode
RUN mkdir -p /app/data && \
    chown -R 1000:1000 /app && \
    chmod -R 755 /app/data

USER user

EXPOSE 7860

# Runs with dynamic PORT fallback (defaults to 7860 for HF Spaces, or respects $PORT on Render)
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]
