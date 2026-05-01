"""
Election Process Assistant – application entry point.

Configures:
- Structured JSON logging (compatible with Google Cloud Logging)
- FastAPI application with OpenAPI metadata
- CORS middleware (configurable via environment)
- Global exception handlers for validation and unhandled errors
- Router registration for all API endpoints
"""

import logging
import logging.config
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.endpoints import router
from app.models.schemas import ErrorDetail

# ---------------------------------------------------------------------------
# Logging configuration – structured JSON for Cloud Logging
# ---------------------------------------------------------------------------

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

LOGGING_CONFIG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            # Use a simple format; Cloud Logging parses structured log fields
            # automatically when the message is valid JSON or key=value pairs.
            "format": (
                '{"severity":"%(levelname)s","message":"%(message)s",'
                '"logger":"%(name)s","time":"%(asctime)s"}'
            ),
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
        },
    },
    "root": {
        "level": _LOG_LEVEL,
        "handlers": ["stdout"],
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan – startup / shutdown hooks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager for startup and shutdown tasks.

    Validates critical environment variables on startup so the service
    fails fast rather than at first request.

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to the running application.
    """
    logger.info("Election Process Assistant starting up.")

    # Fail fast if the API key is not configured
    api_key = os.environ.get("CIVIC_API_KEY", "").strip()
    if not api_key:
        logger.critical(
            "CIVIC_API_KEY is not set. The service cannot function without it. "
            "Set the environment variable and restart."
        )
        # Exit with error code so Cloud Run marks the instance as unhealthy
        sys.exit(1)

    logger.info("CIVIC_API_KEY detected – service is ready.")
    yield
    logger.info("Election Process Assistant shutting down.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured ``FastAPI`` application instance.
    """
    app = FastAPI(
        title="Election Process Assistant",
        description=(
            "A production-ready REST API for querying the Google Civic "
            "Information API v2. Provides election data, polling locations, "
            "contests, and representative information."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # CORS middleware
    # ------------------------------------------------------------------
    allowed_origins_raw = os.environ.get("ALLOWED_ORIGINS", "*")
    allowed_origins = [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # ------------------------------------------------------------------
    # Request timing middleware
    # ------------------------------------------------------------------

    @app.middleware("http")
    async def add_process_time_header(
        request: Request, call_next
    ) -> JSONResponse:
        """Attach X-Process-Time header to every response.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware or route handler.

        Returns:
            HTTP response with added X-Process-Time header.
        """
        start = time.monotonic()
        response = await call_next(request)
        elapsed = round((time.monotonic() - start) * 1000, 2)
        response.headers["X-Process-Time-Ms"] = str(elapsed)
        return response

    # ------------------------------------------------------------------
    # Global exception handlers
    # ------------------------------------------------------------------

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
        """Handle unhandled exceptions as 500 Internal Server Error.

        Args:
            request: The HTTP request that triggered the error.
            exc: The unhandled exception.

        Returns:
            JSON response with HTTP 500 and a generic error message.
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

    # ------------------------------------------------------------------
    # Router registration
    # ------------------------------------------------------------------
    app.include_router(router)

    return app


# Singleton application instance used by the ASGI server
app = create_app()


# ---------------------------------------------------------------------------
# Local development entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        reload=os.environ.get("ENV", "production") == "development",
        log_level=_LOG_LEVEL.lower(),
    )
