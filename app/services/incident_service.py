"""
Incident reporting service.

Persistence strategy (priority order):
  1. Google Cloud Firestore – primary persistent store on GCP.
  2. In-memory list – automatic fallback for local development or when
     Firestore credentials are unavailable.

All incidents are also emitted as structured log entries so Google Cloud
Logging captures them regardless of which persistence layer is active.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.models.schemas import (
    IncidentReportRequest,
    IncidentReportResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory fallback store (thread-safe via asyncio.Lock)
# ---------------------------------------------------------------------------


class _InMemoryIncidentStore:
    """Async-safe in-memory incident repository used when Firestore is absent.

    Attributes:
        _records: List of stored incident dictionaries.
        _lock: Asyncio lock preventing concurrent write corruption.
    """

    def __init__(self) -> None:
        """Initialise an empty in-memory incident store."""
        self._records: list[dict[str, Any]] = []
        self._lock: asyncio.Lock = asyncio.Lock()

    async def add(self, record: dict[str, Any]) -> None:
        """Append a new incident record atomically.

        Args:
            record: Incident data dictionary to persist.
        """
        async with self._lock:
            self._records.append(record)

    async def all(self) -> list[dict[str, Any]]:
        """Return a snapshot of all stored incidents.

        Returns:
            Shallow copy of the incidents list.
        """
        async with self._lock:
            return list(self._records)

    async def get_by_id(self, incident_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a single incident by its UUID.

        Args:
            incident_id: UUID string to look up.

        Returns:
            The matching record dict or ``None`` if not found.
        """
        async with self._lock:
            for rec in self._records:
                if rec.get("incident_id") == incident_id:
                    return rec
        return None


# Module-level in-memory fallback singleton
_memory_store = _InMemoryIncidentStore()


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class IncidentService:
    """Business logic for election integrity incident reporting.

    Uses Google Cloud Firestore as the primary store and falls back
    transparently to an in-memory list when GCP credentials are unavailable.
    All incidents are also emitted as structured Cloud Logging entries for
    independent auditability.
    """

    async def report_incident(
        self, request: IncidentReportRequest
    ) -> IncidentReportResponse:
        """Accept, store, and log an election integrity incident.

        Storage flow:
          1. Try Firestore (``save_incident_to_firestore``).
          2. On failure or unavailability, append to in-memory store.
          3. Emit a structured ``WARNING`` log entry regardless.

        Args:
            request: Validated ``IncidentReportRequest`` from the API layer.

        Returns:
            ``IncidentReportResponse`` with a unique incident ID and status.
        """
        incident_id = str(uuid.uuid4())
        received_at = datetime.now(timezone.utc).isoformat()

        record: dict[str, Any] = {
            "incident_id": incident_id,
            "received_at": received_at,
            "location": request.location,
            "incident_type": request.incident_type.value,
            "severity": request.severity.value,
            "description": request.description,
            "reporter_name": request.reporter_name,
            "reporter_contact": request.reporter_contact,
            "status": "received",
        }

        # ── Primary: Google Cloud Firestore ───────────────────────────────
        from app.services.cloud_services import save_incident_to_firestore  # noqa: PLC0415

        saved_to_firestore = await save_incident_to_firestore(incident_id, record)

        # ── Fallback: in-memory store ─────────────────────────────────────
        if not saved_to_firestore:
            await _memory_store.add(record)
            logger.debug(
                "Incident stored in memory (Firestore unavailable).",
                extra={"incident_id": incident_id},
            )

        # ── Structured audit log (always emitted) ─────────────────────────
        logger.warning(
            "Election integrity incident received.",
            extra={
                "incident_id": incident_id,
                "incident_type": request.incident_type.value,
                "severity": request.severity.value,
                "location": request.location,
                "received_at": received_at,
                "persisted_to": "firestore" if saved_to_firestore else "memory",
            },
        )

        return IncidentReportResponse(
            incident_id=incident_id,
            status="received",
            message=(
                f"Your report (ID: {incident_id}) has been received and securely logged. "
                "For immediate assistance during voting, contact your local election "
                "authority or call the Election Protection Hotline: 1-866-OUR-VOTE."
            ),
        )

    async def list_incidents(self) -> list[dict[str, Any]]:
        """Return all stored incidents from Firestore or in-memory fallback.

        Returns:
            List of all incident record dictionaries.
        """
        from app.services.cloud_services import list_incidents_from_firestore  # noqa: PLC0415

        firestore_results = await list_incidents_from_firestore()
        if firestore_results is not None:
            return firestore_results
        return await _memory_store.all()
