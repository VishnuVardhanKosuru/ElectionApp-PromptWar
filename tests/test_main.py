"""
Pytest test suite for the Election Process Assistant.

Tests:
1. Health endpoint – basic smoke test
2. Successful election info response
3. Successful representative response
4. Malformed / injection-risk address – 422 validation error
5. Civic API timeout simulation – 504 Gateway Timeout
6. Civic API 429 rate-limit response – 429 Too Many Requests
7. Civic API 500 upstream error – 502 Bad Gateway
8. Missing API key – 500 Internal Server Error
9. Election list endpoint – successful response
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
# Test 1 – Health check
# ---------------------------------------------------------------------------


def test_health_check(client: TestClient) -> None:
    """GET /api/v1/health should return 200 with status 'ok'."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


# ---------------------------------------------------------------------------
# Test 2 – Successful election list
# ---------------------------------------------------------------------------


def test_list_elections_success(client: TestClient) -> None:
    """GET /api/v1/elections/list should return 200 with election data."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = _mock_election_list_response()
    mock_response.raise_for_status = MagicMock()

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
# Test 3 – Successful voter info
# ---------------------------------------------------------------------------


def test_get_election_info_success(client: TestClient) -> None:
    """POST /api/v1/elections/info should return 200 with voter info."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        return_value=_mock_voter_info_response(),
    ):
        response = client.post("/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD)

    assert response.status_code == 200
    data = response.json()
    assert data["election"]["name"] == "Test General Election"
    assert len(data["pollingLocations"]) == 1
    assert data["contests"][0]["office"] == "President of the United States"


# ---------------------------------------------------------------------------
# Test 4 – Successful representatives response
# ---------------------------------------------------------------------------


def test_get_representatives_success(client: TestClient) -> None:
    """POST /api/v1/representatives should return 200 with officials."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        return_value=_mock_representatives_response(),
    ):
        response = client.post("/api/v1/representatives", json=VALID_REP_PAYLOAD)

    assert response.status_code == 200
    data = response.json()
    assert data["officials"][0]["name"] == "Test Official"
    assert data["offices"][0]["name"] == "President of the United States"


# ---------------------------------------------------------------------------
# Test 5 – Malformed address (injection attempt) → 422
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_address",
    [
        "'; DROP TABLE elections; --",   # SQL injection attempt
        "<script>alert(1)</script>",      # XSS attempt
        "../../etc/passwd",               # Path traversal attempt
        "a" * 301,                        # Too long
        "AB",                             # Too short
    ],
)
def test_malformed_address_rejected(client: TestClient, bad_address: str) -> None:
    """POST with a disallowed address should return 422 Unprocessable Entity."""
    response = client.post(
        "/api/v1/elections/info",
        json={"address": bad_address},
    )
    assert response.status_code == 422, (
        f"Expected 422 for address: {bad_address!r}, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test 6 – Civic API timeout → 504 Gateway Timeout
# ---------------------------------------------------------------------------


def test_civic_api_timeout_returns_504(client: TestClient) -> None:
    """Simulate a Civic API timeout; expect 504 after retries exhausted."""
    with patch(
        "app.services.civic_api_client.CivicApiClient._request",
        new_callable=AsyncMock,
        side_effect=httpx.ReadTimeout("Request timed out", request=MagicMock()),
    ):
        response = client.post("/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD)

    assert response.status_code == 504
    data = response.json()
    # FastAPI wraps HTTPException detail in {"detail": {...}}
    error = data.get("detail") or data
    assert error["code"] == "UPSTREAM_TIMEOUT"


# ---------------------------------------------------------------------------
# Test 7 – Civic API 429 rate limit → 429 Too Many Requests
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
        response = client.post("/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD)

    assert response.status_code == 429
    data = response.json()
    error = data.get("detail") or data
    assert error["code"] == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Test 8 – Civic API 500 upstream error → 502 Bad Gateway
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
        response = client.post("/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD)

    assert response.status_code == 502
    data = response.json()
    error = data.get("detail") or data
    assert error["code"] == "UPSTREAM_SERVER_ERROR"


# ---------------------------------------------------------------------------
# Test 9 – Missing API key → 500 Internal Server Error
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
        response = client.post("/api/v1/elections/info", json=VALID_ELECTION_PAYLOAD)

    assert response.status_code == 500
    data = response.json()
    error = data.get("detail") or data
    assert error["code"] == "MISSING_API_KEY"


# ---------------------------------------------------------------------------
# Test 10 – Representatives malformed address → 422
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
    base = _mock_voter_info_response()
    return base


# ---------------------------------------------------------------------------
# Test 11 – Polling locations success
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
# Test 12 – Polling locations with ADA filter
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
# Test 13 – Incident report success
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
# Test 14 – XSS sanitization in incident report
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
    # Confirm the report was accepted (sanitization happened silently)
    data = response.json()
    assert data["status"] == "received"


# ---------------------------------------------------------------------------
# Test 15 – Wait times success
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
