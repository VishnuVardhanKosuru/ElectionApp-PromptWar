"""
Election Process Assistant – application entry point (v2.1).

Configures:
- Google Cloud Logging (always-on, graceful local fallback)
- GZip response compression middleware (efficiency)
- Security headers middleware (X-Content-Type-Options, X-Frame-Options, etc.)
- Pydantic v2 settings (``app.config``) for all environment variables
- In-memory TTL cache (``app.cache``) initialised at startup
- FastAPI application with OpenAPI metadata
- CORS middleware (configurable via environment)
- Global exception handlers for validation and unhandled errors
- Router registration for all API endpoints
"""

from __future__ import annotations

import logging
import logging.config
import sys
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.endpoints import router
from app.config import get_settings
from app.models.schemas import ErrorDetail
from app.services.cloud_services import setup_cloud_logging

# ---------------------------------------------------------------------------
# Settings singleton (loaded once per process)
# ---------------------------------------------------------------------------

settings = get_settings()
_LOG_LEVEL_INT = getattr(logging, settings.log_level.upper(), logging.INFO)

# ---------------------------------------------------------------------------
# Logging – Google Cloud Logging first, stdout JSON fallback
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """Set up structured logging.

    Attempts to attach the Google Cloud Logging handler (via
    ``app.services.cloud_services.setup_cloud_logging``).  If GCP credentials
    are not available (local dev), configures a structured JSON-to-stdout
    formatter that Cloud Run's log-collection agent can still parse.
    """
    attached = setup_cloud_logging(log_level=_LOG_LEVEL_INT)
    if not attached:
        # Structured JSON to stdout – Cloud Run captures and forwards to Cloud Logging
        logging.config.dictConfig(
            {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "json": {
                        "format": (
                            '{"severity":"%(levelname)s","message":"%(message)s",'
                            '"logger":"%(name)s","time":"%(asctime)s"}'
                        ),
                        "datefmt": "%Y-%m-%dT%H:%M:%S%z",
                    }
                },
                "handlers": {
                    "stdout": {
                        "class": "logging.StreamHandler",
                        "stream": "ext://sys.stdout",
                        "formatter": "json",
                    }
                },
                "root": {"level": settings.log_level.upper(), "handlers": ["stdout"]},
            }
        )


_configure_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan – startup / shutdown hooks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager for startup and shutdown tasks.

    Startup sequence:
      1. Validate ``CIVIC_API_KEY`` (fail-fast if missing).
      2. Initialise in-memory TTL cache.
      3. Pre-warm Firestore + GCS clients (non-blocking).

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to the running application.
    """
    logger.info(
        "Election Process Assistant v2.1 starting up.",
        extra={"env": settings.env, "port": settings.port},
    )

    # ── 1. Validate API key (fail fast) ───────────────────────────────────
    if not settings.civic_api_key.strip():
        logger.critical(
            "CIVIC_API_KEY is not set. The service cannot start. "
            "Set the environment variable or enable USE_SECRET_MANAGER=true."
        )
        sys.exit(1)

    logger.info("CIVIC_API_KEY validated – service credentials OK.")

    # ── 2. Initialise response cache ──────────────────────────────────────
    from app.cache import init_cache  # noqa: PLC0415

    init_cache(maxsize=settings.cache_maxsize, ttl=settings.cache_ttl_seconds)
    logger.info(
        "Civic API response cache initialised.",
        extra={
            "maxsize": settings.cache_maxsize,
            "ttl_seconds": settings.cache_ttl_seconds,
        },
    )

    # ── 3. Pre-warm Google Cloud clients (non-blocking, best-effort) ──────
    from app.services.cloud_services import get_firestore_client, get_gcs_client  # noqa: PLC0415

    get_firestore_client()   # Warms connection pool; logs success / fallback
    get_gcs_client()         # Same

    logger.info("Election Process Assistant startup complete.")

    yield  # ── Application runs ──────────────────────────────────────────

    logger.info("Election Process Assistant shutting down gracefully.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Fully configured ``FastAPI`` application instance.
    """
    app = FastAPI(
        title="Election Process Assistant",
        description=(
            "A **production-grade, cloud-native** REST API for querying the "
            "Google Civic Information API v2.\n\n"
            "**Google Cloud Integrations:**\n"
            "- 🔐 **Secret Manager** – secure API key retrieval\n"
            "- 📋 **Cloud Logging** – structured log export to Cloud Console\n"
            "- 🗄️ **Firestore** – persistent incident report storage\n"
            "- 🪣 **Cloud Storage** – PDF export archival\n"
            "- 📊 **Cloud Monitoring** – `/api/v1/health` heartbeat probe\n\n"
            "Provides election data, polling locations, contests, and "
            "representative information for any US civic address."
        ),
        version="2.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── GZip compression (efficiency: reduces payload by 60–80%) ──────────
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # ── CORS ──────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # ── Security headers + request timing middleware ───────────────────────

    @app.middleware("http")
    async def security_and_timing_middleware(
        request: Request, call_next
    ) -> JSONResponse:
        """Add security headers and X-Process-Time-Ms to every response.

        Security headers applied:
        - ``X-Content-Type-Options: nosniff`` – prevents MIME sniffing attacks
        - ``X-Frame-Options: DENY`` – prevents clickjacking
        - ``X-XSS-Protection: 1; mode=block`` – legacy XSS filter
        - ``Referrer-Policy: strict-origin-when-cross-origin``
        - ``Cache-Control`` – no caching for API responses

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware / route handler in the chain.

        Returns:
            HTTP response enriched with security and timing headers.
        """
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)

        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Powered-By"] = "Google Cloud Run"
        return response

    # ── Global exception handlers ─────────────────────────────────────────

    @app.exception_handler(ValidationError)
    async def pydantic_validation_handler(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        """Handle Pydantic ``ValidationError`` raised outside route validators.

        Args:
            request: The HTTP request that triggered the error.
            exc: The ``ValidationError`` instance.

        Returns:
            JSON response with HTTP 422 and structured error details.
        """
        logger.warning(
            "Pydantic validation error",
            extra={"path": str(request.url), "errors": exc.errors()},
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorDetail(
                code="VALIDATION_ERROR",
                message="Request payload validation failed.",
                details=exc.errors(),
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Handle unhandled exceptions as HTTP 500 Internal Server Error.

        Args:
            request: The HTTP request that triggered the error.
            exc: The unhandled exception.

        Returns:
            JSON response with HTTP 500 and a safe error message.
        """
        logger.exception(
            "Unhandled exception",
            extra={"path": str(request.url)},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorDetail(
                code="INTERNAL_ERROR",
                message="An unexpected error occurred. Please try again later.",
            ).model_dump(),
        )

    # ── Root route (Welcome & Docs) ───────────────────────────────────────

    @app.get("/", include_in_schema=False)
    async def root():
        """Welcome page for the Election Process Assistant API."""
        return {
            "name": "Election Process Assistant API",
            "version": "2.1.0",
            "status": "operational",
            "documentation": "/docs",
            "health": "/api/v1/health",
            "message": (
                "Welcome to the Election Process Assistant API. "
                "This is a cloud-native service optimized for GCP. "
                "Visit /docs for full API reference."
            ),
        }

    # ── Router registration ───────────────────────────────────────────────
    app.include_router(router)


    return app


# Singleton application instance used by the ASGI server (Gunicorn / Uvicorn)
app = create_app()


# ---------------------------------------------------------------------------
# Local development entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.env == "development",
        log_level=settings.log_level.lower(),
    )
