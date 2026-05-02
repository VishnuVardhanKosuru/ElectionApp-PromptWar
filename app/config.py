"""
Application configuration using Pydantic v2 Settings.

Secret retrieval priority (highest → lowest):
  1. Google Secret Manager (when ``GOOGLE_CLOUD_PROJECT`` is set)
  2. ``CIVIC_API_KEY`` environment variable / ``.env`` file
  3. Cloud Run secret injection (environment variable set by ``--set-secrets``)

All settings are loaded from environment variables with ``.env`` file support.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application-wide configuration resolved from the environment.

    Attributes:
        civic_api_key: Google Civic Information API key (required at runtime).
        log_level: Python log level string (default ``"INFO"``).
        port: TCP port the ASGI server listens on (default ``8080``).
        allowed_origins: Comma-separated CORS-allowed origins.
        env: Runtime environment label (``"production"`` | ``"development"``).
        gcp_project: GCP project ID; auto-detected from ``GOOGLE_CLOUD_PROJECT``.
        secret_name: Secret Manager secret name for the Civic API key.
        use_secret_manager: Explicit override; auto-detected when falsy.
        cache_ttl_seconds: TTL for cached Civic API responses (seconds).
        cache_maxsize: Maximum number of cache entries to keep in memory.
        gcs_bucket: Cloud Storage bucket for PDF export archival (optional).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Core ──────────────────────────────────────────────────────────────
    civic_api_key: str = Field(
        default="",
        description="Google Civic Information API key.",
    )
    log_level: str = Field(default="INFO", description="Python log level.")
    port: int = Field(default=8080, description="ASGI server listening port.")
    allowed_origins: str = Field(
        default="*",
        description="Comma-separated CORS allowed origins.",
    )
    env: str = Field(
        default="production", description="Runtime environment label."
    )

    # ── Google Cloud ───────────────────────────────────────────────────────
    gcp_project: Optional[str] = Field(
        default=None,
        description=(
            "GCP project ID. Auto-detected from GOOGLE_CLOUD_PROJECT env var."
        ),
    )
    secret_name: str = Field(
        default="civic-api-key",
        description="Secret Manager secret name for the Civic API key.",
    )
    use_secret_manager: bool = Field(
        default=False,
        description=(
            "Explicitly enable Secret Manager. Auto-enabled when "
            "gcp_project is set and civic_api_key is empty."
        ),
    )
    gcs_bucket: Optional[str] = Field(
        default=None,
        description="GCS bucket name for PDF export archival.",
    )

    # ── Caching ───────────────────────────────────────────────────────────
    cache_ttl_seconds: int = Field(
        default=300,
        description="TTL (seconds) for Civic API response cache entries.",
    )
    cache_maxsize: int = Field(
        default=512,
        description="Maximum number of entries in the in-memory LRU cache.",
    )

    @model_validator(mode="after")
    def _resolve_gcp_and_secret(self) -> "Settings":
        """Auto-detect GCP project and resolve the API key from Secret Manager.

        Resolution order:
          1. If ``civic_api_key`` is already populated → use it.
          2. If ``GOOGLE_CLOUD_PROJECT`` env var is set → attempt Secret Manager.
          3. Explicit ``use_secret_manager=True`` → require Secret Manager.

        Returns:
            Mutated ``Settings`` instance with ``civic_api_key`` populated.
        """
        # Auto-populate gcp_project from standard GCP env var
        if not self.gcp_project:
            self.gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT") or None

        # Auto-enable Secret Manager when on GCP and no key set yet
        should_use_sm = self.use_secret_manager or (
            bool(self.gcp_project) and not self.civic_api_key.strip()
        )

        if not should_use_sm:
            return self

        if not self.gcp_project:
            logger.warning(
                "USE_SECRET_MANAGER requested but GOOGLE_CLOUD_PROJECT is not set; "
                "falling back to CIVIC_API_KEY env var."
            )
            return self

        from app.services.cloud_services import get_secret  # noqa: PLC0415

        value = get_secret(self.secret_name, self.gcp_project)
        if value:
            self.civic_api_key = value
            logger.info(
                "CIVIC_API_KEY resolved via Google Secret Manager.",
                extra={
                    "secret": self.secret_name,
                    "project": self.gcp_project,
                },
            )
        elif not self.civic_api_key.strip():
            logger.error(
                "Secret Manager lookup failed and CIVIC_API_KEY env var is empty. "
                "Service will fail on first API call."
            )

        return self

    def get_allowed_origins(self) -> list[str]:
        """Parse the ``allowed_origins`` string into a list.

        Returns:
            List of stripped, non-empty origin strings.
        """
        return [
            o.strip() for o in self.allowed_origins.split(",") if o.strip()
        ]

    @property
    def is_gcp(self) -> bool:
        """Return ``True`` when running on Google Cloud Platform.

        Returns:
            ``True`` if a GCP project ID is configured.
        """
        return bool(self.gcp_project)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the application settings singleton (constructed once per process).

    Returns:
        Singleton ``Settings`` instance.
    """
    return Settings()
