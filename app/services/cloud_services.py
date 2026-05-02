"""
Google Cloud services integration module.

Provides unified, always-on access to:
  - Google Cloud Logging  (structured log export to Cloud Console)
  - Google Cloud Secret Manager  (secure API key retrieval)
  - Google Cloud Firestore  (persistent incident report storage)
  - Google Cloud Storage  (PDF export archival)

Each service degrades gracefully when running locally without GCP credentials,
so the application remains fully functional in development without any GCP setup.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cloud Logging – always attempt, fall back to stdout JSON
# ---------------------------------------------------------------------------

_gcp_logging_client: Optional[Any] = None


def setup_cloud_logging(log_level: int = logging.INFO) -> bool:
    """Attach the Google Cloud Logging structured handler to the root logger.

    Uses ``StructuredLogHandler`` (stdout-based) which writes structured JSON
    logs to stdout in the format Cloud Logging can automatically parse when
    running on Cloud Run.  This avoids background gRPC threads, requires no
    network calls, and works identically in local development.

    Args:
        log_level: Python logging level integer (e.g. ``logging.INFO``).

    Returns:
        ``True`` if the Cloud Logging handler was attached, ``False`` otherwise.
    """
    global _gcp_logging_client  # noqa: PLW0603
    try:
        from google.cloud.logging.handlers import (  # type: ignore[import-untyped]
            StructuredLogHandler,
        )

        try:
            # StructuredLogHandler writes structured JSON to stdout – no gRPC,
            # no background threads, no credentials needed locally.
            # Cloud Run captures stdout and auto-ingests into Cloud Logging.
            handler = StructuredLogHandler()
            handler.setLevel(log_level)

            root_logger = logging.getLogger()
            root_logger.setLevel(log_level)
            root_logger.addHandler(handler)

            # Keep a sentinel client for the /gcp/status endpoint
            _gcp_logging_client = True  # type: ignore[assignment]

            logging.getLogger(__name__).info(
                "Google Cloud Logging StructuredLogHandler attached (stdout transport)."
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Cloud Logging handler setup skipped: %s", exc)
            return False
    except ImportError:
        logger.debug(
            "google-cloud-logging not installed; using stdout logging."
        )
        return False


# ---------------------------------------------------------------------------
# Secret Manager – primary API key retrieval
# ---------------------------------------------------------------------------


def get_secret(
    secret_name: str, project_id: Optional[str] = None
) -> Optional[str]:
    """Retrieve the latest version of a secret from Google Secret Manager.

    Args:
        secret_name: The name of the secret (e.g. ``"civic-api-key"``).
        project_id: GCP project ID. Falls back to ``GOOGLE_CLOUD_PROJECT``
            environment variable when ``None``.

    Returns:
        The decoded secret string, or ``None`` if retrieval failed.
    """
    resolved_project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not resolved_project:
        logger.debug(
            "GCP project not configured; skipping Secret Manager lookup."
        )
        return None

    try:
        from google.cloud import secretmanager  # type: ignore[import-untyped]
        from google.api_core.exceptions import (  # type: ignore[import-untyped]
            NotFound,
            PermissionDenied,
        )

        client = secretmanager.SecretManagerServiceClient()
        resource_name = f"projects/{resolved_project}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(
            request={"name": resource_name}
        )
        value = response.payload.data.decode("utf-8").strip()
        logger.info(
            "Secret retrieved from Google Secret Manager.",
            extra={"secret": secret_name, "project": resolved_project},
        )
        return value
    except ImportError:
        logger.debug("google-cloud-secret-manager not installed.")
        return None
    except (NotFound, PermissionDenied) as exc:
        logger.warning(
            "Secret Manager access denied or secret not found.",
            extra={"secret": secret_name, "error": str(exc)},
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Secret Manager retrieval failed.",
            extra={"secret": secret_name, "error": str(exc)},
        )
        return None


# ---------------------------------------------------------------------------
# Firestore – persistent incident storage
# ---------------------------------------------------------------------------

_firestore_client: Optional[Any] = None
_INCIDENTS_COLLECTION = "election_incidents"


def get_firestore_client() -> Optional[Any]:
    """Return a cached Firestore client, or ``None`` if unavailable.

    Returns:
        ``google.cloud.firestore.Client`` instance or ``None``.
    """
    global _firestore_client  # noqa: PLW0603
    if _firestore_client is not None:
        return _firestore_client

    try:
        from google.cloud import firestore  # type: ignore[import-untyped]
        from google.auth.exceptions import DefaultCredentialsError  # type: ignore[import-untyped]

        try:
            _firestore_client = firestore.Client()
            logger.info(
                "Google Cloud Firestore client initialised.",
                extra={"collection": _INCIDENTS_COLLECTION},
            )
            return _firestore_client
        except DefaultCredentialsError:
            logger.debug(
                "Firestore unavailable (no GCP credentials) – using in-memory store."
            )
            return None
    except ImportError:
        logger.debug(
            "google-cloud-firestore not installed – using in-memory store."
        )
        return None


async def save_incident_to_firestore(
    incident_id: str, record: dict[str, Any]
) -> bool:
    """Persist an incident record to Google Cloud Firestore.

    Args:
        incident_id: Unique UUID for the incident (used as document ID).
        record: Incident data dictionary to store.

    Returns:
        ``True`` if saved successfully, ``False`` if Firestore is unavailable.
    """
    client = get_firestore_client()
    if client is None:
        return False

    try:
        doc_ref = client.collection(_INCIDENTS_COLLECTION).document(
            incident_id
        )
        doc_ref.set(record)
        logger.info(
            "Incident saved to Firestore.",
            extra={
                "incident_id": incident_id,
                "collection": _INCIDENTS_COLLECTION,
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to save incident to Firestore; falling back to in-memory store.",
            extra={"incident_id": incident_id, "error": str(exc)},
        )
        return False


async def list_incidents_from_firestore() -> Optional[list[dict[str, Any]]]:
    """Retrieve all incidents from Firestore.

    Returns:
        List of incident dicts, or ``None`` if Firestore is unavailable.
    """
    client = get_firestore_client()
    if client is None:
        return None

    try:
        docs = client.collection(_INCIDENTS_COLLECTION).stream()
        return [doc.to_dict() for doc in docs]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to list incidents from Firestore.",
            extra={"error": str(exc)},
        )
        return None


# ---------------------------------------------------------------------------
# Cloud Storage – PDF export archival
# ---------------------------------------------------------------------------

_gcs_client: Optional[Any] = None


def get_gcs_client() -> Optional[Any]:
    """Return a cached Google Cloud Storage client, or ``None`` if unavailable.

    Returns:
        ``google.cloud.storage.Client`` instance or ``None``.
    """
    global _gcs_client  # noqa: PLW0603
    if _gcs_client is not None:
        return _gcs_client

    try:
        from google.cloud import storage  # type: ignore[import-untyped]
        from google.auth.exceptions import DefaultCredentialsError  # type: ignore[import-untyped]

        try:
            _gcs_client = storage.Client()
            logger.info("Google Cloud Storage client initialised.")
            return _gcs_client
        except DefaultCredentialsError:
            logger.debug("GCS unavailable (no GCP credentials).")
            return None
    except ImportError:
        logger.debug("google-cloud-storage not installed.")
        return None


def upload_pdf_to_gcs(
    pdf_bytes: bytes,
    bucket_name: str,
    blob_name: str,
) -> Optional[str]:
    """Upload a PDF to Google Cloud Storage and return its public URL.

    Args:
        pdf_bytes: Raw PDF content bytes.
        bucket_name: GCS bucket name.
        blob_name: Destination object name (e.g. ``"exports/election_2024.pdf"``).

    Returns:
        Public blob URI (``gs://bucket/blob``) or ``None`` if upload failed.
    """
    client = get_gcs_client()
    if client is None:
        return None

    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(pdf_bytes, content_type="application/pdf")
        uri = f"gs://{bucket_name}/{blob_name}"
        logger.info(
            "PDF uploaded to Google Cloud Storage.",
            extra={"uri": uri},
        )
        return uri
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "GCS upload failed.",
            extra={
                "bucket": bucket_name,
                "blob": blob_name,
                "error": str(exc),
            },
        )
        return None
