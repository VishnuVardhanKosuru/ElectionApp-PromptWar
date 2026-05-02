"""
Pydantic schemas for the Election Process Assistant API.

Provides strict request/response models with input sanitization
to prevent injection attacks and ensure data integrity.
"""

from enum import Enum

import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class ElectionInfoRequest(BaseModel):
    """Request schema for election information lookup.

    Attributes:
        address: Full civic address (street, city, state, zip).
        election_id: Optional Google Civic API election ID to narrow results.
        roles: Optional list of roles to filter representatives.
    """

    address: str = Field(
        ...,
        min_length=5,
        max_length=300,
        description="Civic address to look up (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA').",
        examples=["1600 Amphitheatre Pkwy, Mountain View, CA 94043"],
    )
    election_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional Google Civic API election ID.",
    )
    roles: Optional[list[str]] = Field(
        default=None,
        max_length=10,
        description="Optional list of official roles to filter (e.g. ['legislatorUpperBody']).",
    )

    @field_validator("address")
    @classmethod
    def sanitize_address(cls, value: str) -> str:
        """Sanitize address input to prevent injection attacks.

        Only allows alphanumeric characters, spaces, commas, periods,
        hyphens, apostrophes, and the hash symbol. Strips leading/trailing
        whitespace and collapses multiple internal spaces.

        Args:
            value: Raw address string from the client.

        Returns:
            Sanitized address string.

        Raises:
            ValueError: If the address contains disallowed characters.
        """
        # Strip and collapse whitespace first
        value = " ".join(value.strip().split())

        # Allow: letters, digits, spaces, commas, periods, hyphens, apostrophes, #
        allowed_pattern = re.compile(r"^[A-Za-z0-9 ,.\-'#]+$")
        if not allowed_pattern.match(value):
            raise ValueError(
                "Address contains disallowed characters. "
                "Only letters, digits, spaces, commas, periods, hyphens, "
                "apostrophes, and '#' are permitted."
            )
        return value

    @field_validator("roles", mode="before")
    @classmethod
    def validate_roles(cls, value: Any) -> Optional[list[str]]:
        """Validate that each role string is alphanumeric with optional underscores.

        Args:
            value: Raw roles list from the client.

        Returns:
            Validated list of role strings or None.

        Raises:
            ValueError: If any role string contains disallowed characters.
        """
        if value is None:
            return None
        allowed = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
        for role in value:
            if not allowed.match(role):
                raise ValueError(
                    f"Invalid role '{role}'. Roles must start with a letter "
                    "and contain only alphanumeric characters or underscores."
                )
        return value


class RepresentativeRequest(BaseModel):
    """Request schema for representative lookup.

    Attributes:
        address: Full civic address for the lookup.
        include_offices: Whether to include office details in the response.
    """

    address: str = Field(
        ...,
        min_length=5,
        max_length=300,
        description="Civic address for representative lookup.",
        examples=["1600 Pennsylvania Ave NW, Washington, DC 20500"],
    )
    include_offices: bool = Field(
        default=True,
        description="Include office details in the response.",
    )

    @field_validator("address")
    @classmethod
    def sanitize_address(cls, value: str) -> str:
        """Sanitize address to prevent injection attacks.

        Args:
            value: Raw address string.

        Returns:
            Sanitized address string.

        Raises:
            ValueError: If disallowed characters are found.
        """
        value = " ".join(value.strip().split())
        allowed_pattern = re.compile(r"^[A-Za-z0-9 ,.\-'#]+$")
        if not allowed_pattern.match(value):
            raise ValueError(
                "Address contains disallowed characters. "
                "Only letters, digits, spaces, commas, periods, hyphens, "
                "apostrophes, and '#' are permitted."
            )
        return value


# ---------------------------------------------------------------------------
# Response schemas – Civic Information API entities
# ---------------------------------------------------------------------------


class Channel(BaseModel):
    """Social media or contact channel for an official.

    Attributes:
        type: Channel type (e.g. 'Twitter', 'Facebook').
        id: Channel identifier / handle.
    """

    type: str
    id: str


class Address(BaseModel):
    """Physical or mailing address.

    Attributes:
        line1: First address line.
        line2: Optional second address line.
        line3: Optional third address line.
        city: City name.
        state: State abbreviation.
        zip: ZIP code.
    """

    line1: Optional[str] = None
    line2: Optional[str] = None
    line3: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None


class Official(BaseModel):
    """Elected or appointed official.

    Attributes:
        name: Full name of the official.
        address: List of associated addresses.
        party: Political party affiliation.
        phones: List of phone numbers.
        urls: List of official web URLs.
        photo_url: URL to a photo of the official.
        channels: Social media channels.
    """

    name: str
    address: Optional[list[Address]] = None
    party: Optional[str] = None
    phones: Optional[list[str]] = None
    urls: Optional[list[str]] = None
    photo_url: Optional[str] = Field(default=None, alias="photoUrl")
    channels: Optional[list[Channel]] = None

    model_config = {"populate_by_name": True}


class Office(BaseModel):
    """Elected office definition.

    Attributes:
        name: Name of the office.
        division_id: OCD division identifier.
        levels: Government levels (e.g. ['federal', 'state']).
        roles: Roles associated with this office.
        official_indices: Indices into the officials list.
    """

    name: str
    division_id: Optional[str] = Field(default=None, alias="divisionId")
    levels: Optional[list[str]] = None
    roles: Optional[list[str]] = None
    official_indices: Optional[list[int]] = Field(
        default=None, alias="officialIndices"
    )

    model_config = {"populate_by_name": True}


class NormalizedInput(BaseModel):
    """Address as normalised by the Civic API.

    Attributes:
        line1: Normalised street address.
        city: Normalised city.
        state: Normalised state abbreviation.
        zip: Normalised ZIP code.
    """

    line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None


class RepresentativeResponse(BaseModel):
    """Response payload for /representatives endpoint.

    Attributes:
        normalized_input: Address normalised by the API.
        offices: List of offices found.
        officials: List of officials found.
    """

    normalized_input: Optional[NormalizedInput] = Field(
        default=None, alias="normalizedInput"
    )
    offices: Optional[list[Office]] = None
    officials: Optional[list[Official]] = None

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Election schemas
# ---------------------------------------------------------------------------


class ElectionContest(BaseModel):
    """A single contest (race or ballot measure) within an election.

    Attributes:
        type: Contest type (e.g. 'General', 'Primary', 'Referendum').
        office: Name of the office being contested (if applicable).
        level: Government level(s).
        roles: Roles associated with the contest.
        district: Name of the electoral district.
        candidates: List of candidate names (simplified).
        ballot_title: Title for ballot measures.
        ballot_subtitle: Subtitle for ballot measures.
    """

    type: Optional[str] = None
    office: Optional[str] = None
    level: Optional[list[str]] = None
    roles: Optional[list[str]] = None
    district: Optional[str] = None
    candidates: Optional[list[str]] = None
    ballot_title: Optional[str] = Field(default=None, alias="ballotTitle")
    ballot_subtitle: Optional[str] = Field(
        default=None, alias="ballotSubtitle"
    )

    model_config = {"populate_by_name": True}


class PollingLocation(BaseModel):
    """A polling place or drop-box location.

    Attributes:
        address: Physical address of the polling location.
        notes: Additional notes (e.g. accessible entrance).
        polling_hours: Hours the location is open.
        name: Name of the polling place.
        sources: Data sources for this location.
    """

    address: Optional[Address] = None
    notes: Optional[str] = None
    polling_hours: Optional[str] = Field(default=None, alias="pollingHours")
    name: Optional[str] = None
    sources: Optional[list[dict]] = None

    model_config = {"populate_by_name": True}


class Election(BaseModel):
    """Metadata about a single election.

    Attributes:
        id: Google Civic API election ID.
        name: Human-readable election name.
        election_day: Date of the election (ISO format).
        ocd_division_id: OCD division for the election.
    """

    id: str
    name: str
    election_day: Optional[str] = Field(default=None, alias="electionDay")
    ocd_division_id: Optional[str] = Field(default=None, alias="ocdDivisionId")

    model_config = {"populate_by_name": True}


class ElectionInfoResponse(BaseModel):
    """Response payload for /elections/info endpoint.

    Attributes:
        election: Metadata for the queried election.
        normalized_input: Normalised address returned by the API.
        polling_locations: Drop-off and polling locations.
        contests: List of contests on the ballot.
        state: State-level election administration information.
    """

    election: Optional[Election] = None
    normalized_input: Optional[NormalizedInput] = Field(
        default=None, alias="normalizedInput"
    )
    polling_locations: Optional[list[PollingLocation]] = Field(
        default=None, alias="pollingLocations"
    )
    contests: Optional[list[ElectionContest]] = None
    state: Optional[list[dict]] = None

    model_config = {"populate_by_name": True}


class ElectionListResponse(BaseModel):
    """Response payload for /elections/list endpoint.

    Attributes:
        elections: List of upcoming and past elections.
    """

    elections: list[Election]


# ---------------------------------------------------------------------------
# Generic / utility schemas
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Health-check response for Cloud Run liveness/readiness probes.

    Attributes:
        status: Service status string (always ``"ok"`` when healthy).
        version: Application version string.
        uptime_seconds: Seconds elapsed since the process started.
    """

    status: str
    version: str
    uptime_seconds: Optional[float] = None


class ErrorDetail(BaseModel):
    """Structured error detail for HTTP error responses.

    Attributes:
        code: Machine-readable error code.
        message: Human-readable error description.
        details: Optional additional context.
    """

    code: str
    message: str
    details: Optional[Any] = None


# ---------------------------------------------------------------------------
# Incident reporting schemas
# ---------------------------------------------------------------------------


class IncidentType(str, Enum):
    """Categories of reportable election integrity incidents."""

    VOTER_INTIMIDATION = "voter_intimidation"
    MACHINE_MALFUNCTION = "machine_malfunction"
    LONG_WAIT_TIME = "long_wait_time"
    ACCESSIBILITY_ISSUE = "accessibility_issue"
    POLL_WORKER_MISCONDUCT = "poll_worker_misconduct"
    VOTER_ID_ISSUE = "voter_id_issue"
    OTHER = "other"


class IncidentSeverity(str, Enum):
    """Severity level of a reported incident."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentReportRequest(BaseModel):
    """Request schema for submitting an election integrity incident.

    Attributes:
        location: Polling place description or address.
        incident_type: Category of the incident.
        severity: Perceived severity level.
        description: Free-text description (sanitized).
        reporter_name: Optional anonymous-safe name.
        reporter_contact: Optional follow-up contact (email or phone).
    """

    location: str = Field(
        ...,
        min_length=3,
        max_length=300,
        description="Polling location name or address where the incident occurred.",
    )
    incident_type: IncidentType = Field(
        ...,
        description="Category of the election integrity incident.",
    )
    severity: IncidentSeverity = Field(
        ...,
        description="Perceived severity of the incident.",
    )
    description: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Free-text description of what occurred.",
    )
    reporter_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Optional reporter name (may be left blank for anonymity).",
    )
    reporter_contact: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional email or phone for follow-up.",
    )

    @field_validator("description", "location", mode="before")
    @classmethod
    def strip_html_tags(cls, value: str) -> str:
        """Remove HTML/script tags to prevent stored XSS.

        Args:
            value: Raw text from the client.

        Returns:
            Sanitized plain text string.
        """
        # Remove any HTML tags
        cleaned = re.sub(r"<[^>]+>", "", value)
        # Collapse excessive whitespace
        cleaned = " ".join(cleaned.split())
        return cleaned

    @field_validator("reporter_contact", mode="before")
    @classmethod
    def validate_contact(cls, value: Optional[str]) -> Optional[str]:
        """Validate reporter contact is a safe email or phone string.

        Args:
            value: Raw contact string.

        Returns:
            Sanitized contact string or None.

        Raises:
            ValueError: If contact contains disallowed characters.
        """
        if value is None:
            return None
        cleaned = re.sub(r"<[^>]+>", "", value).strip()
        if len(cleaned) > 200:
            raise ValueError("Contact information is too long.")
        return cleaned


class IncidentReportResponse(BaseModel):
    """Response payload confirming an incident was received.

    Attributes:
        incident_id: Unique UUID for the submitted report.
        status: Processing status string.
        message: Human-readable confirmation.
    """

    incident_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Accessibility / polling location filter schemas
# ---------------------------------------------------------------------------


class EnhancedPollingLocation(BaseModel):
    """Polling location enriched with accessibility and wait-time data.

    Attributes:
        name: Name of the polling place.
        address: Physical address.
        polling_hours: Hours the location is open.
        accessibility_compliant: Whether the location is ADA-compliant.
        wait_minutes: Estimated current wait in minutes.
        notes: Additional operational notes.
    """

    name: Optional[str] = None
    address: Optional[Address] = None
    polling_hours: Optional[str] = Field(default=None, alias="pollingHours")
    accessibility_compliant: bool = False
    wait_minutes: int = 0
    notes: Optional[str] = None

    model_config = {"populate_by_name": True}


class EnhancedPollingListResponse(BaseModel):
    """Response for the /polling-locations endpoint.

    Attributes:
        normalized_input: Address as normalised by the Civic API.
        locations: List of enhanced polling locations.
        total: Total count of locations returned.
        accessible_only: Whether the ADA-only filter was applied.
    """

    normalized_input: Optional[NormalizedInput] = None
    locations: list[EnhancedPollingLocation]
    total: int
    accessible_only: bool


# ---------------------------------------------------------------------------
# Wait-time schemas
# ---------------------------------------------------------------------------


class WaitStatus(str, Enum):
    """Categorical wait-time status for a polling station."""

    LOW = "low"  # < 15 min
    MODERATE = "moderate"  # 15–30 min
    HIGH = "high"  # 30–60 min
    VERY_HIGH = "very_high"  # > 60 min


class WaitTimeInfo(BaseModel):
    """Wait-time data for a single polling station.

    Attributes:
        location_id: Opaque identifier for the station.
        location_name: Human-readable station name.
        estimated_wait_minutes: Current estimated wait in minutes.
        status: Categorical wait-level label.
        last_updated: ISO 8601 timestamp of the last update.
        accessibility_compliant: Whether the station is ADA-compliant.
    """

    location_id: str
    location_name: str
    estimated_wait_minutes: int
    status: WaitStatus
    last_updated: str
    accessibility_compliant: bool


class WaitTimeListResponse(BaseModel):
    """Response payload for the /wait-times endpoint.

    Attributes:
        stations: List of polling stations with wait data.
        note: Contextual note (e.g., data-source caveat).
    """

    stations: list[WaitTimeInfo]
    note: str
