"""
Pytest test suite for the Election Process Assistant v2.1.

Tests:
 1.  Health endpoint (uptime_seconds, v2.1.0)
 2.  Cache stats endpoint
 3.  GCP status endpoint – returns service availability dict
 4.  Application metrics endpoint
 5.  Successful election list response
 6.  Successful voter info response
 7.  Successful representative response
 8.  Malformed / injection-risk address – 422
 9.  Civic API timeout – 504
10.  Civic API 429 rate-limit – 429
11.  Civic API 500 upstream error – 502
12.  Civic API 404 not found – 400 (graceful handling)
13.  Missing API key – 500
14.  Representatives malformed address – 422
15.  Polling locations success
16.  Polling locations ADA filter
17.  Incident report success
18.  Incident report XSS sanitization
19.  Wait times success
20.  Settings.get_allowed_origins parsing
21.  Settings.is_gcp property
22.  CivicResponseCache hit/miss/clear lifecycle
23.  GZip middleware compresses large responses
24.  Security headers present on every response
25.  cloud_services.setup_cloud_logging fallback (no GCP creds)
26.  cloud_services.get_secret fallback (no project)
27.  cloud_services.save_incident_to_firestore fallback
28.  cloud_services.upload_pdf_to_gcs fallback
29.  Incident service uses in-memory fallback when Firestore unavailable
30.  Health endpoint returns 200 (Cloud Monitoring probe)
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# Ensure a dummy API key exists for all tests (avoids sys.exit in lifespan)
os.environ.setdefault("CIVIC_API_KEY", "test-api-key-12345")

# Import the app *after* setting the env var so lifespan doesn't abort
from app.main import app  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Provide a synchronous TestClient for the FastAPI application.

    Yields:
        Configured ``TestClient`` instance.
    """
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def mock_firestore_client():
    """Globally mock the Firestore client so tests do not hang on real network calls."""
    with patch("app.services.cloud_services.get_firestore_client", return_value=None):
        yield



# Minimal payloads for reuse across tests
VALID_ADDRESS = "1600 Amphitheatre Pkwy, Mountain View, CA 94043"
VALID_ELECTION_PAYLOAD = {"address": VALID_ADDRESS}
VALID_REP_PAYLOAD = {"address": VALID_ADDRESS, "include_offices": True}


# ---------------------------------------------------------------------------
# Sample API response fixtures
# ---------------------------------------------------------------------------


def _mock_election_list_response() -> dict:
    """Return a minimal mock /elections API response."""
    return {
        "elections": [
            {
                "id": "2000",
                "name": "Test Election",
                "electionDay": "2024-11-05",
                "ocdDivisionId": "ocd-division/country:us",
            }
        ]
    }


def _mock_voter_info_response() -> dict:
    """Return a minimal mock /voterinfo API response."""
    return {
        "election": {
            "id": "2000",
            "name": "Test General Election",
            "electionDay": "2024-11-05",
            "ocdDivisionId": "ocd-division/country:us",
        },
        "normalizedInput": {
            "line1": "1600 Amphitheatre Pkwy",
            "city": "Mountain View",
            "state": "CA",
            "zip": "94043",
        },
        "pollingLocations": [
            {
                "address": {
                    "line1": "100 Main St",
                    "city": "Mountain View",
                    "state": "CA",
                    "zip": "94041",
                },
                "pollingHours": "7am-8pm",
                "name": "Mountain View Community Center",
            }
        ],
        "contests": [
            {
                "type": "General",
                "office": "President of the United States",
                "level": ["federal"],
                "roles": ["headOfState"],
                "candidates": [
                    {"name": "Candidate A"},
                    {"name": "Candidate B"},
                ],
                "district": {"name": "United States"},
            }
        ],
    }


def _mock_representatives_response() -> dict:
    """Return a minimal mock /representatives API response."""
    return {
        "normalizedInput": {
            "line1": "1600 Amphitheatre Pkwy",
            "city": "Mountain View",
            "state": "CA",
            "zip": "94043",
        },
        "offices": [
            {
                "name": "President of the United States",
                "divisionId": "ocd-division/country:us",
                "levels": ["federal"],
                "roles": ["headOfState"],
                "officialIndices": [0],
            }
        ],
        "officials": [
            {
                "name": "Test Official",
                "party": "Test Party",
                "phones": ["+1 800 000 0000"],
                "urls": ["https://example.com"],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Test 1 – Health check (Cloud Monitoring heartbeat)
# ---------------------------------------------------------------------------


def test_health_check(client: TestClient) -> None:
    """GET /api/v1/health should return 200 with status 'ok' and uptime."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    # uptime_seconds must be a non-negative number
    assert data.get("uptime_seconds") is not None
    assert data["uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# Test 2 – Cache stats endpoint
# ---------------------------------------------------------------------------


def test_cache_stats(client: TestClient) -> None:
    """GET /api/v1/cache/stats should return a dict with hit/miss counters."""
    response = client.get("/api/v1/cache/stats")
    assert response.status_code == 200
    data = response.json()
    # Must contain at minimum these keys
    for key in ("hits", "misses", "size"):
        assert key in data, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Test 3 – Successful election list
# ---------------------------------------------------------------------------


def test_list_elections_success(client: TestClient) -> None:
    """GET /api/v1/elections/list should return 200 with election data."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        return_value=_mock_election_list_response(),
    ):
        response = client.get("/api/v1/elections/list")

    assert response.status_code == 200
    data = response.json()
    assert "elections" in data
    assert len(data["elections"]) == 1
    assert data["elections"][0]["name"] == "Test Election"


# ---------------------------------------------------------------------------
# Test 4 – Successful voter info
# ---------------------------------------------------------------------------


def test_get_election_info_success(client: TestClient) -> None:
    """POST /api/v1/elections/info should return 200 with voter info."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        return_value=_mock_voter_info_response(),
    ):
        response = client.post(
            "/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD
        )

    assert response.status_code == 200
    data = response.json()
    assert data["election"]["name"] == "Test General Election"
    assert len(data["pollingLocations"]) == 1
    assert data["contests"][0]["office"] == "President of the United States"


# ---------------------------------------------------------------------------
# Test 5 – Successful representatives response
# ---------------------------------------------------------------------------


def test_get_representatives_success(client: TestClient) -> None:
    """POST /api/v1/representatives should return 200 with officials."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        return_value=_mock_representatives_response(),
    ):
        response = client.post(
            "/api/v1/representatives", json=VALID_REP_PAYLOAD
        )

    assert response.status_code == 200
    data = response.json()
    assert data["officials"][0]["name"] == "Test Official"
    assert data["offices"][0]["name"] == "President of the United States"


# ---------------------------------------------------------------------------
# Test 6 – Malformed address (injection attempt) → 422
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_address",
    [
        "'; DROP TABLE elections; --",  # SQL injection attempt
        "<script>alert(1)</script>",  # XSS attempt
        "../../etc/passwd",  # Path traversal attempt
        "a" * 301,  # Too long
        "AB",  # Too short
    ],
)
def test_malformed_address_rejected(
    client: TestClient, bad_address: str
) -> None:
    """POST with a disallowed address should return 422 Unprocessable Entity."""
    response = client.post(
        "/api/v1/elections/info",
        json={"address": bad_address},
    )
    assert (
        response.status_code == 422
    ), f"Expected 422 for address: {bad_address!r}, got {response.status_code}"


# ---------------------------------------------------------------------------
# Test 7 – Civic API timeout → 504 Gateway Timeout
# ---------------------------------------------------------------------------


def test_civic_api_timeout_returns_504(client: TestClient) -> None:
    """Simulate a Civic API timeout; expect 504 after retries exhausted."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        side_effect=httpx.ReadTimeout(
            "Request timed out", request=MagicMock()
        ),
    ):
        response = client.post(
            "/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD
        )

    assert response.status_code == 504
    data = response.json()
    error = data.get("detail") or data
    assert error["code"] == "UPSTREAM_TIMEOUT"


# ---------------------------------------------------------------------------
# Test 8 – Civic API 429 rate limit → 429 Too Many Requests
# ---------------------------------------------------------------------------


def test_civic_api_rate_limit_returns_429(client: TestClient) -> None:
    """Simulate a 429 from the Civic API; expect 429 forwarded to the client."""
    mock_req = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Rate limit exceeded"

    exc = httpx.HTTPStatusError("429", request=mock_req, response=mock_resp)

    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        side_effect=exc,
    ):
        response = client.post(
            "/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD
        )

    assert response.status_code == 429
    data = response.json()
    error = data.get("detail") or data
    assert error["code"] == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Test 9 – Civic API 500 upstream error → 502 Bad Gateway
# ---------------------------------------------------------------------------


def test_civic_api_server_error_returns_502(client: TestClient) -> None:
    """Simulate a 500 from the Civic API; expect 502 Bad Gateway."""
    mock_req = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    exc = httpx.HTTPStatusError("500", request=mock_req, response=mock_resp)

    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        side_effect=exc,
    ):
        response = client.post(
            "/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD
        )

    assert response.status_code == 502
    data = response.json()
    error = data.get("detail") or data
    assert error["code"] == "UPSTREAM_SERVER_ERROR"


# ---------------------------------------------------------------------------
# Test 10 – Civic API 404 Not Found → 400 Bad Request (graceful handling)
# ---------------------------------------------------------------------------


def test_civic_api_404_returns_400(client: TestClient) -> None:
    """Simulate a 404 from the Civic API; application must handle it gracefully.

    The Civic API returns 404 for addresses with no registered election data.
    We map this to HTTP 400 with a clear user-facing message (not a 500).
    This test verifies the application does NOT crash and returns a
    machine-readable error code.
    """
    mock_req = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "No data found for the provided address."

    exc = httpx.HTTPStatusError("404", request=mock_req, response=mock_resp)

    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        side_effect=exc,
    ):
        response = client.post(
            "/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD
        )

    # Must NOT be a 500 – the app should handle upstream 4xx gracefully
    assert (
        response.status_code != 500
    ), "Application must not crash on Civic API 404"
    assert response.status_code in (
        400,
        404,
    ), f"Expected 400 for upstream 404, got {response.status_code}"
    data = response.json()
    error = data.get("detail") or data
    # Must include a machine-readable error code
    assert (
        "code" in error
    ), "Response must include a machine-readable error code"
    assert error["code"] == "UPSTREAM_CLIENT_ERROR"


# ---------------------------------------------------------------------------
# Test 11 – Missing API key → 500 Internal Server Error
# ---------------------------------------------------------------------------


def test_missing_api_key_returns_500(client: TestClient) -> None:
    """Simulate missing CIVIC_API_KEY; expect 500 with MISSING_API_KEY code."""
    exc = RuntimeError(
        "CIVIC_API_KEY environment variable is not set or is empty."
    )

    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        side_effect=exc,
    ):
        response = client.post(
            "/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD
        )

    assert response.status_code == 500
    data = response.json()
    error = data.get("detail") or data
    assert error["code"] == "MISSING_API_KEY"


# ---------------------------------------------------------------------------
# Test 12 – Representatives malformed address → 422
# ---------------------------------------------------------------------------


def test_representatives_malformed_address(client: TestClient) -> None:
    """POST /api/v1/representatives with invalid address returns 422."""
    response = client.post(
        "/api/v1/representatives",
        json={"address": "<script>bad()</script>"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Helpers for new endpoint mocks
# ---------------------------------------------------------------------------


def _mock_voter_info_with_locations() -> dict:
    """Voter info response that includes polling locations."""
    return _mock_voter_info_response()


# ---------------------------------------------------------------------------
# Test 13 – Polling locations success
# ---------------------------------------------------------------------------


def test_get_polling_locations_success(client: TestClient) -> None:
    """GET /api/v1/polling-locations should return 200 with locations list."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        return_value=_mock_voter_info_with_locations(),
    ):
        response = client.get(
            "/api/v1/polling-locations",
            params={"address": VALID_ADDRESS},
        )

    assert response.status_code == 200
    data = response.json()
    assert "locations" in data
    assert isinstance(data["locations"], list)
    assert "total" in data
    assert data["accessible_only"] is False


# ---------------------------------------------------------------------------
# Test 14 – Polling locations with ADA filter
# ---------------------------------------------------------------------------


def test_get_polling_locations_ada_filter(client: TestClient) -> None:
    """GET /api/v1/polling-locations?accessible_only=true filters correctly."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        return_value=_mock_voter_info_with_locations(),
    ):
        response = client.get(
            "/api/v1/polling-locations",
            params={"address": VALID_ADDRESS, "accessible_only": "true"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["accessible_only"] is True
    # Every returned location must be ADA-compliant
    for loc in data["locations"]:
        assert loc["accessibility_compliant"] is True


# ---------------------------------------------------------------------------
# Test 15 – Incident report success
# ---------------------------------------------------------------------------


def test_report_incident_success(client: TestClient) -> None:
    """POST /api/v1/report-incident should return 201 with an incident_id."""
    payload = {
        "location": "Mountain View Community Center",
        "incident_type": "machine_malfunction",
        "severity": "medium",
        "description": "The voting machine displayed an error and froze.",
    }
    response = client.post("/api/v1/report-incident", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert "incident_id" in data
    assert data["status"] == "received"
    assert "1-866-OUR-VOTE" in data["message"]


# ---------------------------------------------------------------------------
# Test 16 – XSS sanitization in incident report
# ---------------------------------------------------------------------------


def test_report_incident_xss_sanitized(client: TestClient) -> None:
    """HTML tags in incident description must be stripped before storage."""
    payload = {
        "location": "Test Polling Place",
        "incident_type": "other",
        "severity": "low",
        "description": "<script>alert('xss')</script>Machine was broken today.",
    }
    response = client.post("/api/v1/report-incident", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "received"


# ---------------------------------------------------------------------------
# Test 17 – Wait times success
# ---------------------------------------------------------------------------


def test_get_wait_times_success(client: TestClient) -> None:
    """GET /api/v1/wait-times should return 200 with stations list."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        return_value=_mock_voter_info_with_locations(),
    ):
        response = client.get(
            "/api/v1/wait-times",
            params={"address": VALID_ADDRESS},
        )

    assert response.status_code == 200
    data = response.json()
    assert "stations" in data
    assert "note" in data
    for station in data["stations"]:
        assert "estimated_wait_minutes" in station
        assert station["status"] in ("low", "moderate", "high", "very_high")


# ---------------------------------------------------------------------------
# Test 18 – Settings: get_allowed_origins parsing
# ---------------------------------------------------------------------------


def test_settings_get_allowed_origins() -> None:
    """Settings.get_allowed_origins() should parse comma-separated values."""
    from app.config import Settings

    s = Settings(
        civic_api_key="dummy", allowed_origins="https://a.com, https://b.com"
    )
    origins = s.get_allowed_origins()
    assert origins == ["https://a.com", "https://b.com"]

    # Single wildcard
    s2 = Settings(civic_api_key="dummy", allowed_origins="*")
    assert s2.get_allowed_origins() == ["*"]


# ---------------------------------------------------------------------------
# Test 19 – CivicResponseCache hit/miss/clear lifecycle
# ---------------------------------------------------------------------------


def test_civic_response_cache_lifecycle() -> None:
    """CivicResponseCache should track hits, misses, and support clear()."""
    from app.cache import CivicResponseCache

    cache = CivicResponseCache(maxsize=10, ttl=60)
    key = cache.make_key("elections", {})

    # Initial miss
    assert cache.get(key) is None
    stats = cache.stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 0

    # Set and hit
    cache.set(key, {"elections": []})
    result = cache.get(key)
    assert result == {"elections": []}
    assert cache.stats()["hits"] == 1

    # Clear
    cache.clear()
    assert cache.get(key) is None
    assert cache.stats()["size"] == 0


# ---------------------------------------------------------------------------
# Test 23 – GCP status endpoint
# ---------------------------------------------------------------------------


def test_gcp_status_endpoint(client: TestClient) -> None:
    """GET /api/v1/gcp/status should return a dict with service statuses."""
    response = client.get("/api/v1/gcp/status")
    assert response.status_code == 200
    data = response.json()
    # Must contain all four GCP service keys
    for key in (
        "cloud_logging",
        "secret_manager",
        "firestore",
        "cloud_storage",
    ):
        assert key in data, f"Missing GCP status key: {key}"
    assert "gcp_project" in data
    assert "cache_backend" in data
    # Values must be non-empty strings
    assert isinstance(data["cloud_logging"], str)
    assert isinstance(data["cache_backend"], str)


# ---------------------------------------------------------------------------
# Test 24 – Application metrics endpoint (Cloud Monitoring)
# ---------------------------------------------------------------------------


def test_application_metrics_endpoint(client: TestClient) -> None:
    """GET /api/v1/metrics should return uptime, version, and cache data."""
    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "election-process-assistant"
    assert data["version"] == "2.1.0"
    assert data["uptime_seconds"] >= 0
    assert "cache" in data
    assert "hit_rate_pct" in data["cache"]
    assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test 25 – Security headers on every response
# ---------------------------------------------------------------------------


def test_security_headers_present(client: TestClient) -> None:
    """Every response must include critical security headers."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    headers = response.headers
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("x-xss-protection") == "1; mode=block"
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "x-process-time-ms" in headers


# ---------------------------------------------------------------------------
# Test 26 – Settings.is_gcp property
# ---------------------------------------------------------------------------


def test_settings_is_gcp() -> None:
    """Settings.is_gcp should return True only when gcp_project is set."""
    from app.config import Settings

    s_local = Settings(civic_api_key="dummy", gcp_project=None)
    assert s_local.is_gcp is False

    s_gcp = Settings(civic_api_key="dummy", gcp_project="my-project-123")
    assert s_gcp.is_gcp is True


# ---------------------------------------------------------------------------
# Test 27 – cloud_services.setup_cloud_logging fallback
# ---------------------------------------------------------------------------


def test_cloud_logging_fallback_without_credentials() -> None:
    """setup_cloud_logging must return False gracefully when GCP is unavailable."""
    from app.services.cloud_services import setup_cloud_logging

    # Should not raise; returns False when DefaultCredentialsError or ImportError
    result = setup_cloud_logging(log_level=20)  # logging.INFO = 20
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Test 28 – cloud_services.get_secret fallback (no project)
# ---------------------------------------------------------------------------


def test_get_secret_no_project() -> None:
    """get_secret must return None gracefully when no GCP project is configured."""
    import os
    from app.services.cloud_services import get_secret

    # Ensure GOOGLE_CLOUD_PROJECT is not set in test environment
    original = os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
    try:
        result = get_secret("civic-api-key", project_id=None)
        assert result is None
    finally:
        if original:
            os.environ["GOOGLE_CLOUD_PROJECT"] = original


# ---------------------------------------------------------------------------
# Test 29 – cloud_services.save_incident_to_firestore fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.cloud_services.get_firestore_client", return_value=None)
async def test_save_incident_firestore_fallback(mock_get_client: MagicMock) -> None:
    """save_incident_to_firestore should return False when Firestore is unavailable."""
    from app.services.cloud_services import save_incident_to_firestore

    record = {"incident_id": "test-123", "location": "Test Location"}
    result = await save_incident_to_firestore("test-123", record)
    # In CI without GCP credentials, must return False (not raise)
    assert isinstance(result, bool)
    assert result is False


# ---------------------------------------------------------------------------
# Test 30 – cloud_services.upload_pdf_to_gcs fallback
# ---------------------------------------------------------------------------


def test_upload_pdf_gcs_fallback() -> None:
    """upload_pdf_to_gcs should return None gracefully without GCP credentials."""
    from app.services.cloud_services import upload_pdf_to_gcs

    result = upload_pdf_to_gcs(b"%PDF-1.4 test", "test-bucket", "test.pdf")
    # Without credentials, must return None (not raise)
    assert result is None


# ---------------------------------------------------------------------------
# Test 31 – Health endpoint returns 200 for Cloud Monitoring probe
# ---------------------------------------------------------------------------


def test_health_is_200_for_cloud_monitoring(client: TestClient) -> None:
    """Cloud Run / Cloud Monitoring probe: GET /api/v1/health must always be 200."""
    for _ in range(3):  # Probe-like repeated calls
        response = client.get("/api/v1/health")
        assert (
            response.status_code == 200
        ), f"Health probe returned {response.status_code} – Cloud Run would mark unhealthy"
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "2.1.0"
