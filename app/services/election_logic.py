"""
Election logic service layer.

Bridges the raw Civic API client responses with the structured
Pydantic response models consumed by the API controllers.
Handles business logic, data shaping, and error classification.
"""

import logging
from typing import Any, Optional

import httpx

from app.models.schemas import (
    Address,
    Channel,
    Election,
    ElectionContest,
    ElectionInfoResponse,
    ElectionListResponse,
    NormalizedInput,
    Office,
    Official,
    PollingLocation,
    RepresentativeResponse,
)
from app.services.civic_api_client import CivicApiClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _map_address(raw: Optional[dict[str, Any]]) -> Optional[Address]:
    """Map a raw address dict to an ``Address`` Pydantic model.

    Args:
        raw: Raw address dictionary from the API response (may be None).

    Returns:
        Populated ``Address`` instance or ``None`` if input is falsy.
    """
    if not raw:
        return None
    return Address(
        line1=raw.get("line1"),
        line2=raw.get("line2"),
        line3=raw.get("line3"),
        city=raw.get("city"),
        state=raw.get("state"),
        zip=raw.get("zip"),
    )


def _map_normalized_input(
    raw: Optional[dict[str, Any]],
) -> Optional[NormalizedInput]:
    """Map a raw normalizedInput dict to a ``NormalizedInput`` model.

    Args:
        raw: Raw normalizedInput dictionary from the API (may be None).

    Returns:
        Populated ``NormalizedInput`` instance or ``None``.
    """
    if not raw:
        return None
    return NormalizedInput(
        line1=raw.get("line1"),
        city=raw.get("city"),
        state=raw.get("state"),
        zip=raw.get("zip"),
    )


def _map_official(raw: dict[str, Any]) -> Official:
    """Map a raw official dict to an ``Official`` Pydantic model.

    Args:
        raw: Raw official dictionary from the Civic API.

    Returns:
        Populated ``Official`` instance.
    """
    addresses = [_map_address(a) for a in raw.get("address", [])]
    channels = [
        Channel(type=ch.get("type", ""), id=ch.get("id", ""))
        for ch in raw.get("channels", [])
    ]
    return Official(
        name=raw.get("name", "Unknown"),
        address=[a for a in addresses if a] or None,
        party=raw.get("party"),
        phones=raw.get("phones"),
        urls=raw.get("urls"),
        photoUrl=raw.get("photoUrl"),
        channels=channels or None,
    )


def _map_office(raw: dict[str, Any]) -> Office:
    """Map a raw office dict to an ``Office`` Pydantic model.

    Args:
        raw: Raw office dictionary from the Civic API.

    Returns:
        Populated ``Office`` instance.
    """
    return Office(
        name=raw.get("name", "Unknown Office"),
        divisionId=raw.get("divisionId"),
        levels=raw.get("levels"),
        roles=raw.get("roles"),
        officialIndices=raw.get("officialIndices"),
    )


def _map_election(raw: dict[str, Any]) -> Election:
    """Map a raw election dict to an ``Election`` Pydantic model.

    Args:
        raw: Raw election dictionary from the Civic API.

    Returns:
        Populated ``Election`` instance.
    """
    return Election(
        id=str(raw.get("id", "")),
        name=raw.get("name", "Unknown Election"),
        electionDay=raw.get("electionDay"),
        ocdDivisionId=raw.get("ocdDivisionId"),
    )


def _map_contest(raw: dict[str, Any]) -> ElectionContest:
    """Map a raw contest dict to an ``ElectionContest`` Pydantic model.

    Extracts candidate names from nested candidate objects for simplicity.

    Args:
        raw: Raw contest dictionary from the Civic API.

    Returns:
        Populated ``ElectionContest`` instance.
    """
    candidates = [
        c.get("name", "Unknown")
        for c in raw.get("candidates", [])
        if isinstance(c, dict)
    ]
    district_raw = raw.get("district", {})
    return ElectionContest(
        type=raw.get("type"),
        office=raw.get("office"),
        level=raw.get("level"),
        roles=raw.get("roles"),
        district=(
            district_raw.get("name")
            if isinstance(district_raw, dict)
            else None
        ),
        candidates=candidates or None,
        ballotTitle=raw.get("ballotTitle"),
        ballotSubtitle=raw.get("ballotSubtitle"),
    )


def _map_polling_location(raw: dict[str, Any]) -> PollingLocation:
    """Map a raw pollingLocation dict to a ``PollingLocation`` Pydantic model.

    Args:
        raw: Raw polling location dictionary from the Civic API.

    Returns:
        Populated ``PollingLocation`` instance.
    """
    addr_raw = raw.get("address", {})
    address = _map_address(addr_raw) if addr_raw else None
    return PollingLocation(
        address=address,
        notes=raw.get("notes"),
        pollingHours=raw.get("pollingHours"),
        name=raw.get("name"),
        sources=raw.get("sources"),
    )


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class ElectionService:
    """Business logic layer for election-related operations.

    All methods are stateless and safe for concurrent execution inside
    Cloud Run instances.

    Args:
        client: An instance of ``CivicApiClient`` (injected for testability).
    """

    def __init__(self, client: CivicApiClient) -> None:
        """Initialise the service with the Civic API client.

        Args:
            client: Async Civic Information API client.
        """
        self._client = client

    # ------------------------------------------------------------------
    # Elections
    # ------------------------------------------------------------------

    async def list_elections(self) -> ElectionListResponse:
        """Retrieve the list of all available elections.

        Returns:
            ``ElectionListResponse`` containing mapped election objects.

        Raises:
            httpx.HTTPStatusError: For API-level errors.
            RuntimeError: For client configuration errors.
        """
        logger.info("Fetching election list from Civic API.")
        raw = await self._client.get_elections()
        elections = [_map_election(e) for e in raw.get("elections", [])]
        logger.info("Fetched %d elections.", len(elections))
        return ElectionListResponse(elections=elections)

    async def get_election_info(
        self,
        address: str,
        election_id: Optional[int] = None,
    ) -> ElectionInfoResponse:
        """Retrieve voter information for an address and optional election ID.

        Args:
            address: Sanitised civic address string.
            election_id: Optional election ID to scope the query.

        Returns:
            ``ElectionInfoResponse`` with election, polling locations, and contests.

        Raises:
            httpx.HTTPStatusError: For API-level errors (e.g. 400, 404).
            RuntimeError: For client configuration errors.
        """
        logger.info(
            "Fetching voter info for address.",
            extra={"election_id": election_id},
        )
        raw = await self._client.get_voter_info(address, election_id)

        election = (
            _map_election(raw["election"]) if raw.get("election") else None
        )
        normalized = _map_normalized_input(raw.get("normalizedInput"))
        polling_locs = [
            _map_polling_location(p) for p in raw.get("pollingLocations", [])
        ]
        contests = [_map_contest(c) for c in raw.get("contests", [])]

        logger.info(
            "Voter info fetched.",
            extra={
                "polling_locations": len(polling_locs),
                "contests": len(contests),
            },
        )
        return ElectionInfoResponse(
            election=election,
            normalizedInput=normalized,
            pollingLocations=polling_locs or None,
            contests=contests or None,
            state=raw.get("state"),
        )

    # ------------------------------------------------------------------
    # Representatives
    # ------------------------------------------------------------------

    async def get_representatives(
        self,
        address: str,
        roles: Optional[list[str]] = None,
        include_offices: bool = True,
    ) -> RepresentativeResponse:
        """Retrieve representative information for a given address.

        Args:
            address: Sanitised civic address string.
            roles: Optional list of role filters.
            include_offices: Whether to include office details.

        Returns:
            ``RepresentativeResponse`` with offices and officials.

        Raises:
            httpx.HTTPStatusError: For API-level errors.
            RuntimeError: For client configuration errors.
        """
        logger.info(
            "Fetching representatives.",
            extra={"roles": roles, "include_offices": include_offices},
        )
        raw = await self._client.get_representatives(
            address, roles, include_offices
        )

        normalized = _map_normalized_input(raw.get("normalizedInput"))
        offices = [_map_office(o) for o in raw.get("offices", [])]
        officials = [_map_official(o) for o in raw.get("officials", [])]

        logger.info(
            "Representatives fetched.",
            extra={
                "offices": len(offices),
                "officials": len(officials),
            },
        )
        return RepresentativeResponse(
            normalizedInput=normalized,
            offices=offices or None,
            officials=officials or None,
        )

    # ------------------------------------------------------------------
    # Rate-limit detection helper
    # ------------------------------------------------------------------

    @staticmethod
    def is_rate_limited(exc: httpx.HTTPStatusError) -> bool:
        """Check if an ``HTTPStatusError`` represents a rate-limit response.

        Args:
            exc: The HTTP status error to inspect.

        Returns:
            ``True`` if the response status is 429, ``False`` otherwise.
        """
        return exc.response.status_code == 429
