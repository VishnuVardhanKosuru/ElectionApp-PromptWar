"""
Async client wrapper for the Google Civic Information API v2.

Provides:
- Async HTTP requests via ``httpx``
- Exponential backoff with jitter for transient failures
- Structured logging for Cloud Logging compatibility
- API key sourced exclusively from environment variables
"""

import asyncio
import logging
import os
import random
import time
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://www.googleapis.com/civicinfo/v2/"
_DEFAULT_TIMEOUT = 10.0  # seconds
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5  # seconds
_BACKOFF_MAX = 8.0  # seconds
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_api_key() -> str:
    """Retrieve the Google Civic API key from the environment.

    Returns:
        The API key string.

    Raises:
        RuntimeError: If the ``CIVIC_API_KEY`` environment variable is not set.
    """
    api_key = os.environ.get("CIVIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "CIVIC_API_KEY environment variable is not set or is empty. "
            "Set it before starting the application."
        )
    return api_key


def _compute_backoff(attempt: int) -> float:
    """Compute exponential backoff with full jitter.

    Uses the 'full jitter' strategy recommended by AWS to avoid thundering herd.

    Args:
        attempt: Zero-based retry attempt number.

    Returns:
        Sleep duration in seconds.
    """
    cap = _BACKOFF_MAX
    base = _BACKOFF_BASE * (2**attempt)
    sleep = random.uniform(0, min(cap, base))  # noqa: S311 – not crypto use
    return sleep


# ---------------------------------------------------------------------------
# Client class
# ---------------------------------------------------------------------------


class CivicApiClient:
    """Async HTTP client for the Google Civic Information API v2.

    Designed to be used as an async context manager or instantiated once
    and shared across the application lifetime (stateless).

    Example::

        async with CivicApiClient() as client:
            data = await client.get_elections()

    Attributes:
        _timeout: HTTP request timeout in seconds.
        _client: Underlying ``httpx.AsyncClient`` instance.
    """

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        """Initialise the client.

        Args:
            timeout: HTTP request timeout (seconds). Defaults to 10 s.
        """
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "CivicApiClient":
        """Start the underlying HTTP client session."""
        await self._start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Close the underlying HTTP client session."""
        await self._close()

    async def _start(self) -> None:
        """Create the ``httpx.AsyncClient`` with sensible defaults."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        logger.debug("CivicApiClient HTTP session started.")

    async def _close(self) -> None:
        """Gracefully close the ``httpx.AsyncClient``."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug("CivicApiClient HTTP session closed.")

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute an authenticated HTTP request with retry / backoff.

        Args:
            method: HTTP verb (e.g. ``"GET"``).
            endpoint: API path relative to the base URL.
            params: Optional query parameters (API key is injected automatically).

        Returns:
            Parsed JSON response body as a dictionary.

        Raises:
            httpx.HTTPStatusError: For non-retryable 4xx errors.
            httpx.TimeoutException: If all retries are exhausted due to timeouts.
            RuntimeError: For unexpected failures after all retries.
        """
        if self._client is None:
            await self._start()

        api_key = _get_api_key()
        query: dict[str, Any] = {"key": api_key}
        if params:
            query.update(params)

        url = urljoin(_BASE_URL, endpoint)
        last_exc: Optional[Exception] = None

        for attempt in range(_MAX_RETRIES):
            start_ts = time.monotonic()
            try:
                logger.info(
                    "Civic API request",
                    extra={
                        "method": method,
                        "url": url,
                        "attempt": attempt + 1,
                    },
                )
                response = await self._client.request(  # type: ignore[union-attr]
                    method, url, params=query
                )
                elapsed = time.monotonic() - start_ts

                logger.info(
                    "Civic API response",
                    extra={
                        "status_code": response.status_code,
                        "elapsed_ms": round(elapsed * 1000, 2),
                        "url": url,
                    },
                )

                if response.status_code in _RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "Retryable status code received",
                        extra={
                            "status_code": response.status_code,
                            "attempt": attempt + 1,
                        },
                    )
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    sleep = _compute_backoff(attempt)
                    logger.debug("Backing off for %.2fs before retry.", sleep)
                    await asyncio.sleep(sleep)
                    continue

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as exc:
                elapsed = time.monotonic() - start_ts
                logger.warning(
                    "Civic API request timed out",
                    extra={
                        "attempt": attempt + 1,
                        "elapsed_ms": round(elapsed * 1000, 2),
                        "url": url,
                    },
                )
                last_exc = exc
                sleep = _compute_backoff(attempt)
                await asyncio.sleep(sleep)

            except httpx.HTTPStatusError as exc:
                # Non-retryable 4xx – surface immediately
                logger.error(
                    "Non-retryable HTTP error from Civic API",
                    extra={
                        "status_code": exc.response.status_code,
                        "body": exc.response.text[:500],
                        "url": url,
                    },
                )
                raise

        logger.error(
            "All retries exhausted for Civic API request",
            extra={"url": url, "max_retries": _MAX_RETRIES},
        )
        if last_exc:
            raise last_exc
        raise RuntimeError(f"All {_MAX_RETRIES} retries exhausted for {url}.")

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_elections(self) -> dict[str, Any]:
        """Fetch the list of available elections from the Civic API.

        Returns:
            Raw API response dict containing an ``elections`` key.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
            RuntimeError: If the API key is missing or retries are exhausted.
        """
        return await self._request("GET", "elections")

    async def get_voter_info(
        self,
        address: str,
        election_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Fetch voter information for a given address and optional election.

        Args:
            address: Civic address string (sanitised by the caller).
            election_id: Optional Google Civic election ID to scope the query.

        Returns:
            Raw API response dict with election, polling location, and contest data.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
            RuntimeError: If the API key is missing or retries are exhausted.
        """
        params: dict[str, Any] = {"address": address, "returnAllAvailableData": True}
        if election_id is not None:
            params["electionId"] = election_id
        return await self._request("GET", "voterinfo", params=params)

    async def get_representatives(
        self,
        address: str,
        roles: Optional[list[str]] = None,
        include_offices: bool = True,
    ) -> dict[str, Any]:
        """Fetch representatives for a given address.

        Args:
            address: Civic address string (sanitised by the caller).
            roles: Optional list of roles to filter (e.g. ``['headOfState']``).
            include_offices: Whether to include office details in the response.

        Returns:
            Raw API response dict with offices and officials data.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
            RuntimeError: If the API key is missing or retries are exhausted.
        """
        params: dict[str, Any] = {
            "address": address,
            "includeOffices": str(include_offices).lower(),
        }
        if roles:
            # The Civic API accepts repeated ``roles`` query parameters
            # httpx handles lists as repeated params automatically
            params["roles"] = roles
        return await self._request("GET", "representatives", params=params)
