# ============================================================
# Stage 1 – Builder
# Installs only production dependencies into a virtual env.
# This stage will NOT be included in the final image.
# ============================================================
FROM python:3.12-slim AS builder

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Copy only the requirements file first to leverage Docker layer caching
COPY requirements.txt .

# Create an isolated virtual environment and install production deps only.
# Exclude test packages (pytest, pytest-asyncio, respx) via grep filter.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    grep -Ev "^(pytest|respx|#|-$)" requirements.txt | \
    grep -v "^$" > /tmp/prod-requirements.txt && \
    /opt/venv/bin/pip install -r /tmp/prod-requirements.txt


# ============================================================
# Stage 2 – Runtime
# Copies only the virtual env and application source code.
# No compilers, no build tools, minimal attack surface.
# ============================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Point PATH to our virtual env so "python" resolves correctly
    PATH="/opt/venv/bin:$PATH" \
    # Default port – Cloud Run injects PORT at runtime
    PORT=8080 \
    # Logging level – override with Cloud Run env var
    LOG_LEVEL=INFO

# Create a non-root user to run the application (security best practice)
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

# Health check – Cloud Run will also probe /api/v1/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/v1/health')" || exit 1

# Start Uvicorn with a single worker per container instance.
# Cloud Run scales horizontally; adding more workers here wastes RAM.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "1", \
     "--log-level", "info", \
     "--no-access-log"]
