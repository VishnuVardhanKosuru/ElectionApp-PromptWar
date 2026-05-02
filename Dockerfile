# ============================================================
# Stage 1 – Builder
# Installs only production dependencies into an isolated venv.
# This stage is NOT included in the final image.
# Base: python:3.11-slim (required per architecture spec)
# ============================================================
FROM python:3.11-slim AS builder

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Copy only the requirements file first to leverage Docker layer caching
COPY requirements.txt .

# Create an isolated virtual environment and install production deps only.
# Exclude test packages (pytest, respx, pytest-cov, pytest-asyncio) and
# comments / blank lines.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    grep -Ev "^(pytest|respx|#|-$)" requirements.txt | \
    grep -v "^$" > /tmp/prod-requirements.txt && \
    /opt/venv/bin/pip install -r /tmp/prod-requirements.txt


# ============================================================
# Stage 2 – Runtime
# Copies only the virtual env and application source code.
# No compilers, no build tools – minimal attack surface.
# Base: python:3.11-slim (matches builder for ABI compatibility)
# ============================================================
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Point PATH to our virtual env so "python" resolves correctly
    PATH="/opt/venv/bin:$PATH" \
    # Default port – Cloud Run injects PORT at runtime
    PORT=8080 \
    # Logging level – override with Cloud Run env var
    LOG_LEVEL=INFO \
    # Number of Gunicorn workers; 2-4×(CPU cores)+1 is the standard formula.
    # Cloud Run typically gives 1 vCPU → use 2 workers.
    WEB_CONCURRENCY=2

# Create a non-root user to run the application (Least Privilege principle)
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup --no-create-home appuser

WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application source code
COPY app/ ./app/

# Transfer ownership to the non-root user
RUN chown -R appuser:appgroup /app

# Switch to the non-root user
USER appuser

# Expose the application port
EXPOSE 8080

# Health check – Cloud Run also probes /api/v1/health via its readiness probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/v1/health')" \
        || exit 1

# Production startup: Gunicorn manages WEB_CONCURRENCY Uvicorn workers.
# - Gunicorn handles OS-level worker lifecycle (respawn on crash, graceful reload).
# - UvicornWorker provides async I/O within each worker process.
# - --timeout 0 disables sync-worker timeout (Uvicorn workers are async).
CMD ["sh", "-c", \
     "gunicorn app.main:app \
      --worker-class uvicorn.workers.UvicornWorker \
      --workers ${WEB_CONCURRENCY} \
      --bind 0.0.0.0:${PORT} \
      --timeout 0 \
      --access-logfile - \
      --error-logfile - \
      --log-level info"]
