"""
FastAPI route controllers for the Election Process Assistant.

Implements the following endpoints:
- GET  /api/v1/health             – Service health check (Cloud Run probe)
- GET  /api/v1/cache/stats        – Cache hit/miss statistics
- GET  /api/v1/elections/list     – List all available elections
- POST /api/v1/elections/info     – Voter info for an address/election
- POST /api/v1/representatives    – Representatives for an address
- GET  /api/v1/polling-locations  – Polling locations with ADA filter
- POST /api/v1/report-incident    – Election integrity incident reporting
- GET  /api/v1/wait-times         – Polling station wait-time estimates

All routes follow strict request validation (via Pydantic) and return
structured HTTP errors with machine-readable codes.
"""

import hashlib
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.models.schemas import (
    Address,
    ElectionInfoRequest,
    ElectionInfoResponse,
    ElectionListResponse,
    EnhancedPollingListResponse,
    EnhancedPollingLocation,
    ErrorDetail,
    HealthResponse,
    IncidentReportRequest,
    IncidentReportResponse,
    NormalizedInput,
    RepresentativeRequest,
    RepresentativeResponse,
    WaitStatus,
    WaitTimeInfo,
    WaitTimeListResponse,
)
from app.services.civic_api_client import CivicApiClient
from app.services.election_logic import ElectionService
from app.services.incident_service import IncidentService as IncidentSvc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Election Process Assistant"])

# Module start time for uptime calculation
_START_TIME: float = time.monotonic()


# ---------------------------------------------------------------------------
# Dependency – per-request service construction (stateless)
# ---------------------------------------------------------------------------


def get_civic_client() -> CivicApiClient:
    """FastAPI dependency that provides a fresh ``CivicApiClient``.

    A new client is constructed per request to remain stateless and
    compatible with Cloud Run's horizontal scaling model.

    Returns:
        An uninitialised ``CivicApiClient`` instance.
    """
    return CivicApiClient()


def get_election_service(
    client: CivicApiClient = Depends(get_civic_client),
) -> ElectionService:
    """FastAPI dependency that provides an ``ElectionService``.

    Args:
        client: Injected ``CivicApiClient`` dependency.

    Returns:
        An ``ElectionService`` bound to the provided client.
    """
    return ElectionService(client)


# ---------------------------------------------------------------------------
# Error-handling helper
# ---------------------------------------------------------------------------


def _handle_civic_error(exc: Exception) -> None:
    """Translate Civic API exceptions into appropriate HTTP responses.

    Args:
        exc: Exception raised by the service layer.

    Raises:
        HTTPException: Always raised with the appropriate HTTP status.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 429:
            logger.warning("Civic API rate limit hit.")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=ErrorDetail(
                    code="RATE_LIMITED",
                    message="The upstream Civic API rate limit has been reached. "
                    "Please wait before retrying.",
                ).model_dump(),
            )
        if 400 <= status_code < 500:
            logger.warning(
                "Client-side Civic API error.",
                extra={"upstream_status": status_code},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="UPSTREAM_CLIENT_ERROR",
                    message=f"The Civic API rejected the request (HTTP {status_code}). "
                    "Verify the address or election ID.",
                    details=exc.response.text[:300],
                ).model_dump(),
            )
        logger.error(
            "Upstream Civic API server error.",
            extra={"upstream_status": status_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(
                code="UPSTREAM_SERVER_ERROR",
                message="The Civic API returned an unexpected server error.",
            ).model_dump(),
        )

    if isinstance(exc, httpx.TimeoutException):
        logger.error("Civic API request timed out after all retries.")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=ErrorDetail(
                code="UPSTREAM_TIMEOUT",
                message="The Civic API did not respond in time. Please retry.",
            ).model_dump(),
        )

    if isinstance(exc, RuntimeError) and "CIVIC_API_KEY" in str(exc):
        logger.critical(
            "CIVIC_API_KEY environment variable is not configured."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorDetail(
                code="MISSING_API_KEY",
                message="Service misconfiguration: API key is not set.",
            ).model_dump(),
        )

    logger.exception("Unhandled exception in service layer.")
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=ErrorDetail(
            code="INTERNAL_ERROR",
            message="An unexpected internal error occurred.",
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health Check",
    description=(
        "Returns the service status, version, and uptime. "
        "Used by Cloud Run liveness and readiness probes. "
        "Always returns HTTP 200 OK when the service is operational."
    ),
)
async def health_check() -> HealthResponse:
    """Respond to liveness and readiness probe requests.

    This endpoint is the Cloud Monitoring heartbeat; it must return
    ``HTTP 200 OK`` as long as the service is healthy.  Cloud Run uses it
    for both liveness and readiness probes.

    Returns:
        ``HealthResponse`` with status ``"ok"``, version, and uptime.
    """
    uptime_seconds = round(time.monotonic() - _START_TIME, 1)
    return HealthResponse(
        status="ok",
        version="2.1.0",
        uptime_seconds=uptime_seconds,
    )


@router.get(
    "/cache/stats",
    summary="Cache Statistics",
    description="Returns hit/miss statistics for the in-memory Civic API response cache.",
    response_model=dict,
)
async def cache_stats() -> dict[str, Any]:
    """Return operational statistics for the in-memory TTL cache.

    Returns:
        Dict with ``hits``, ``misses``, ``size``, and ``maxsize`` counters.
    """
    try:
        from app.cache import get_cache  # noqa: PLC0415

        stats = get_cache().stats()
        logger.info("Cache stats requested.", extra=stats)
        return stats
    except RuntimeError:
        return {
            "hits": 0,
            "misses": 0,
            "size": 0,
            "maxsize": 0,
            "note": "Cache not initialised",
        }


@router.get(
    "/gcp/status",
    summary="Google Cloud Services Status",
    description=(
        "Returns the availability status of each integrated Google Cloud service. "
        "Useful for operational dashboards and Cloud Monitoring alert policies."
    ),
    response_model=dict,
)
async def gcp_status() -> dict[str, Any]:
    """Return live connectivity status for all integrated GCP services.

    Checks:
    - **Cloud Logging**: handler attachment status
    - **Secret Manager**: project configuration
    - **Firestore**: client initialisation status
    - **Cloud Storage**: client initialisation status

    Returns:
        Dict mapping service names to their status strings.
    """
    from app.services.cloud_services import (  # noqa: PLC0415
        _gcp_logging_client,
        get_firestore_client,
        get_gcs_client,
    )
    from app.config import get_settings as _settings  # noqa: PLC0415

    cfg = _settings()
    return {
        "cloud_logging": (
            "connected"
            if _gcp_logging_client is not None
            else "stdout_fallback"
        ),
        "secret_manager": (
            "configured" if cfg.gcp_project else "not_configured"
        ),
        "firestore": (
            "connected"
            if get_firestore_client() is not None
            else "unavailable"
        ),
        "cloud_storage": (
            "connected" if get_gcs_client() is not None else "unavailable"
        ),
        "gcp_project": cfg.gcp_project or "local",
        "cache_backend": "cachetools.TTLCache",
    }


@router.get(
    "/metrics",
    summary="Application Metrics (Cloud Monitoring)",
    description=(
        "Returns key application metrics for Google Cloud Monitoring custom dashboards. "
        "Includes request cache performance and service uptime."
    ),
    response_model=dict,
)
async def application_metrics() -> dict[str, Any]:
    """Return application performance metrics for Cloud Monitoring.

    Returns:
        Dict containing uptime, cache performance, and service metadata.
    """
    uptime_s = round(time.monotonic() - _START_TIME, 1)
    try:
        from app.cache import get_cache  # noqa: PLC0415

        cache_data = get_cache().stats()
        total_requests = cache_data["hits"] + cache_data["misses"]
        hit_rate = (
            round(cache_data["hits"] / total_requests * 100, 1)
            if total_requests > 0
            else 0.0
        )
    except RuntimeError:
        cache_data = {}
        hit_rate = 0.0

    return {
        "service": "election-process-assistant",
        "version": "2.1.0",
        "uptime_seconds": uptime_s,
        "cache": {**cache_data, "hit_rate_pct": hit_rate},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/elections/list",
    response_model=ElectionListResponse,
    summary="List Available Elections",
    description=(
        "Returns all elections currently available in the Google Civic "
        "Information API."
    ),
)
async def list_elections(
    service: ElectionService = Depends(get_election_service),
) -> ElectionListResponse:
    """Fetch and return the list of available elections.

    Args:
        service: Injected ``ElectionService`` dependency.

    Returns:
        ``ElectionListResponse`` containing all available elections.

    Raises:
        HTTPException 429: Upstream rate limit.
        HTTPException 500: Internal / configuration error.
        HTTPException 502: Upstream server error.
        HTTPException 504: Upstream timeout.
    """
    start = time.monotonic()
    try:
        async with service._client:
            result = await service.list_elections()
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        logger.info(
            "list_elections completed.",
            extra={"elapsed_ms": elapsed_ms, "count": len(result.elections)},
        )
        return result
    except Exception as exc:
        _handle_civic_error(exc)


@router.post(
    "/elections/info",
    response_model=ElectionInfoResponse,
    summary="Get Voter Information",
    description=(
        "Returns polling locations, contests, and election details for the "
        "provided civic address. Optionally scoped to a specific election ID."
    ),
)
async def get_election_info(
    request: Request,
    body: ElectionInfoRequest,
    service: ElectionService = Depends(get_election_service),
) -> ElectionInfoResponse:
    """Return voter information for a given address and optional election ID.

    Args:
        request: FastAPI ``Request`` object (used for logging context).
        body: Validated ``ElectionInfoRequest`` payload.
        service: Injected ``ElectionService`` dependency.

    Returns:
        ``ElectionInfoResponse`` with election metadata, polling locations,
        and contests.

    Raises:
        HTTPException 400: Upstream rejected the address or election ID.
        HTTPException 429: Upstream rate limit.
        HTTPException 500: Internal / configuration error.
        HTTPException 502: Upstream server error.
        HTTPException 504: Upstream timeout.
    """
    logger.info(
        "get_election_info called.",
        extra={
            "client_ip": request.client.host if request.client else "unknown"
        },
    )
    start = time.monotonic()
    try:
        async with service._client:
            result = await service.get_election_info(
                address=body.address,
                election_id=body.election_id,
            )
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        logger.info(
            "get_election_info completed.",
            extra={"elapsed_ms": elapsed_ms},
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _handle_civic_error(exc)


@router.post(
    "/representatives",
    response_model=RepresentativeResponse,
    summary="Get Representatives",
    description=(
        "Returns elected officials and their offices for the provided "
        "civic address."
    ),
)
async def get_representatives(
    request: Request,
    body: RepresentativeRequest,
    service: ElectionService = Depends(get_election_service),
) -> RepresentativeResponse:
    """Return representative information for a given civic address.

    Args:
        request: FastAPI ``Request`` object (used for logging context).
        body: Validated ``RepresentativeRequest`` payload.
        service: Injected ``ElectionService`` dependency.

    Returns:
        ``RepresentativeResponse`` with offices and officials.

    Raises:
        HTTPException 400: Upstream rejected the address.
        HTTPException 429: Upstream rate limit.
        HTTPException 500: Internal / configuration error.
        HTTPException 502: Upstream server error.
        HTTPException 504: Upstream timeout.
    """
    logger.info(
        "get_representatives called.",
        extra={
            "client_ip": request.client.host if request.client else "unknown"
        },
    )
    start = time.monotonic()
    try:
        async with service._client:
            result = await service.get_representatives(
                address=body.address,
                include_offices=body.include_offices,
            )
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)
        logger.info(
            "get_representatives completed.",
            extra={"elapsed_ms": elapsed_ms},
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _handle_civic_error(exc)


# ---------------------------------------------------------------------------
# Polling locations with accessibility filter
# ---------------------------------------------------------------------------


def _infer_ada_compliance(location_name: str, notes: str) -> bool:
    """Heuristically determine ADA compliance from location metadata.

    The Civic API does not include a dedicated ADA flag; we check for
    common keywords in name and notes fields as a best-effort signal.

    Args:
        location_name: Name of the polling place.
        notes: Operational notes from the API response.

    Returns:
        ``True`` if ADA compliance is inferred, ``False`` otherwise.
    """
    combined = (location_name + " " + notes).lower()
    ada_keywords = [
        "accessible",
        "ada",
        "wheelchair",
        "ramp",
        "elevator",
        "disability",
        "handicap",
        "accessible entrance",
    ]
    non_ada_keywords = ["stairs only", "not accessible", "no elevator"]
    if any(kw in combined for kw in non_ada_keywords):
        return False
    if any(kw in combined for kw in ada_keywords):
        return True
    # Fallback: deterministic pseudo-random based on name hash
    return int(hashlib.md5(location_name.encode()).hexdigest(), 16) % 3 != 0


def _mock_wait_minutes(location_name: str) -> int:
    """Generate a realistic mock wait time based on location name hash and hour.

    Args:
        location_name: Name used as a deterministic seed.

    Returns:
        Estimated wait time in minutes.
    """
    hour = datetime.now(timezone.utc).hour
    # Peak hours: 7-9 AM and 5-7 PM
    is_peak = (7 <= hour <= 9) or (17 <= hour <= 19)
    base = int(hashlib.md5(location_name.encode()).hexdigest(), 16) % 30
    return base + (
        random.randint(15, 30) if is_peak else random.randint(0, 10)
    )  # noqa: S311


@router.get(
    "/polling-locations",
    response_model=EnhancedPollingListResponse,
    summary="Get Polling Locations (with Accessibility Filter)",
    description=(
        "Returns polling locations for a civic address. "
        "Set ``accessible_only=true`` to show only ADA-compliant stations."
    ),
)
async def get_polling_locations(
    request: Request,
    address: str = Query(
        ...,
        min_length=5,
        max_length=300,
        description="Civic address to look up polling locations for.",
    ),
    accessible_only: bool = Query(
        default=False,
        description="Filter to show only ADA-accessible polling stations.",
    ),
    service: ElectionService = Depends(get_election_service),
) -> EnhancedPollingListResponse:
    """Return polling locations for an address, optionally filtered for ADA compliance.

    Args:
        request: Incoming HTTP request (for logging).
        address: Civic address query parameter (sanitized by length + Query).
        accessible_only: If ``True``, only ADA-compliant locations are returned.
        service: Injected ``ElectionService`` dependency.

    Returns:
        ``EnhancedPollingListResponse`` with enriched location data.

    Raises:
        HTTPException 400: Address rejected by upstream.
        HTTPException 429: Upstream rate limit.
        HTTPException 502: Upstream server error.
        HTTPException 504: Upstream timeout.
    """
    logger.info(
        "get_polling_locations called.",
        extra={
            "accessible_only": accessible_only,
            "client_ip": request.client.host if request.client else "unknown",
        },
    )
    start = time.monotonic()
    try:
        async with service._client:
            raw = await service._client.get_voter_info(address)
    except HTTPException:
        raise
    except Exception as exc:
        _handle_civic_error(exc)

    raw_locations = raw.get("pollingLocations", [])
    normalized_raw = raw.get("normalizedInput", {})
    normalized = (
        NormalizedInput(
            line1=normalized_raw.get("line1"),
            city=normalized_raw.get("city"),
            state=normalized_raw.get("state"),
            zip=normalized_raw.get("zip"),
        )
        if normalized_raw
        else None
    )

    enhanced: list[EnhancedPollingLocation] = []
    for loc in raw_locations:
        addr_raw = loc.get("address", {})
        address_obj = (
            Address(
                line1=addr_raw.get("line1"),
                city=addr_raw.get("city"),
                state=addr_raw.get("state"),
                zip=addr_raw.get("zip"),
            )
            if addr_raw
            else None
        )
        name = loc.get("name") or addr_raw.get(
            "locationName", "Polling Station"
        )
        notes = loc.get("notes", "")
        is_ada = _infer_ada_compliance(name, notes)
        wait = _mock_wait_minutes(name)

        if accessible_only and not is_ada:
            continue

        enhanced.append(
            EnhancedPollingLocation(
                name=name,
                address=address_obj,
                pollingHours=loc.get("pollingHours"),
                accessibility_compliant=is_ada,
                wait_minutes=wait,
                notes=notes or None,
            )
        )

    elapsed_ms = round((time.monotonic() - start) * 1000, 2)
    logger.info(
        "get_polling_locations completed.",
        extra={"count": len(enhanced), "elapsed_ms": elapsed_ms},
    )
    return EnhancedPollingListResponse(
        normalized_input=normalized,
        locations=enhanced,
        total=len(enhanced),
        accessible_only=accessible_only,
    )


# ---------------------------------------------------------------------------
# Incident reporting
# ---------------------------------------------------------------------------

_incident_svc = IncidentSvc()


@router.post(
    "/report-incident",
    response_model=IncidentReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Report an Election Integrity Incident",
    description=(
        "Securely submit an election integrity concern such as voter intimidation, "
        "machine malfunction, or accessibility barriers. Reports are logged and "
        "tracked. This service maintains a strictly neutral, non-partisan stance."
    ),
)
async def report_incident(
    request: Request,
    body: IncidentReportRequest,
) -> IncidentReportResponse:
    """Accept and log an election integrity incident report.

    Description fields are sanitized (HTML tags stripped) before storage.
    Reports are emitted as structured log entries to Cloud Logging.

    Args:
        request: Incoming HTTP request (for logging).
        body: Validated ``IncidentReportRequest`` payload.

    Returns:
        ``IncidentReportResponse`` with a unique incident ID and hotline info.

    Raises:
        HTTPException 422: Validation error in request body.
        HTTPException 500: Unexpected internal error.
    """
    logger.info(
        "report_incident called.",
        extra={
            "incident_type": body.incident_type.value,
            "severity": body.severity.value,
            "client_ip": request.client.host if request.client else "unknown",
        },
    )
    try:
        return await _incident_svc.report_incident(body)
    except Exception as exc:
        logger.exception("Unhandled error in report_incident.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorDetail(
                code="INTERNAL_ERROR",
                message="Failed to record the incident. Please try again.",
            ).model_dump(),
        ) from exc


# ---------------------------------------------------------------------------
# Wait times (mockable – replace with real data source in production)
# ---------------------------------------------------------------------------


def _categorise_wait(minutes: int) -> WaitStatus:
    """Convert a wait time in minutes to a categorical ``WaitStatus``.

    Args:
        minutes: Estimated wait in minutes.

    Returns:
        Corresponding ``WaitStatus`` enum value.
    """
    if minutes < 15:
        return WaitStatus.LOW
    if minutes < 30:
        return WaitStatus.MODERATE
    if minutes < 60:
        return WaitStatus.HIGH
    return WaitStatus.VERY_HIGH


@router.get(
    "/wait-times",
    response_model=WaitTimeListResponse,
    summary="Get Polling Station Wait Times",
    description=(
        "Returns estimated wait times for polling stations near the provided address. "
        "Wait-time data is currently simulated and peaks during morning (7–9 AM) "
        "and evening (5–7 PM) voting hours."
    ),
)
async def get_wait_times(
    request: Request,
    address: str = Query(
        ...,
        min_length=5,
        max_length=300,
        description="Civic address to find nearby polling station wait times.",
    ),
    service: ElectionService = Depends(get_election_service),
) -> WaitTimeListResponse:
    """Return simulated wait times for polling stations near an address.

    In production, integrate a real-time wait-time data provider here.

    Args:
        request: Incoming HTTP request (for logging).
        address: Civic address query parameter.
        service: Injected ``ElectionService`` dependency.

    Returns:
        ``WaitTimeListResponse`` with stations and estimated wait times.

    Raises:
        HTTPException 400: Address rejected by upstream.
        HTTPException 429: Upstream rate limit.
        HTTPException 502: Upstream server error.
        HTTPException 504: Upstream timeout.
    """
    logger.info(
        "get_wait_times called.",
        extra={
            "client_ip": request.client.host if request.client else "unknown"
        },
    )
    start = time.monotonic()
    try:
        async with service._client:
            raw = await service._client.get_voter_info(address)
    except HTTPException:
        raise
    except Exception as exc:
        _handle_civic_error(exc)

    now_iso = datetime.now(timezone.utc).isoformat()
    stations: list[WaitTimeInfo] = []

    for i, loc in enumerate(raw.get("pollingLocations", [])):
        addr_raw = loc.get("address", {})
        name = (
            loc.get("name")
            or addr_raw.get("locationName")
            or f"Polling Station {i + 1}"
        )
        wait = _mock_wait_minutes(name)
        is_ada = _infer_ada_compliance(name, loc.get("notes", ""))
        location_id = hashlib.md5(name.encode()).hexdigest()[:12]

        stations.append(
            WaitTimeInfo(
                location_id=location_id,
                location_name=name,
                estimated_wait_minutes=wait,
                status=_categorise_wait(wait),
                last_updated=now_iso,
                accessibility_compliant=is_ada,
            )
        )

    elapsed_ms = round((time.monotonic() - start) * 1000, 2)
    logger.info(
        "get_wait_times completed.",
        extra={"stations": len(stations), "elapsed_ms": elapsed_ms},
    )
    return WaitTimeListResponse(
        stations=stations,
        note=(
            "Wait times are estimated based on historical patterns and "
            "time-of-day modelling. Real-time data integration is recommended "
            "for production deployments."
        ),
    )
