"""Optional, last-good ingestion for historical weather and Indian holidays.

These adapters never run during application startup.  Each source owns a separate,
schema-validated cache so an unavailable public API cannot affect the operational model or
destroy the other context source.  Weather observations use warehouse-city centroids as a
documented proxy; they are contextual observations, not route-level conditions.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Self

import httpx
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

CONTEXT_CACHE_SCHEMA_VERSION = 1
DEFAULT_CONTEXT_DATE_FROM = date(2025, 1, 1)
DEFAULT_CONTEXT_DATE_TO = date(2026, 6, 30)
DEFAULT_WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_HOLIDAY_URL = "https://date.nager.at/api/v3/PublicHolidays"
DEFAULT_HOLIDAY_FALLBACK_URL = (
    "https://calendar.google.com/calendar/ical/"
    "en.indian%23holiday%40group.v.calendar.google.com/public/basic.ics"
)


class ContextIngestionError(RuntimeError):
    """Raised when an optional public source cannot produce a complete snapshot."""


def _date(value: object) -> date:
    if isinstance(value, datetime):
        raise ValueError("expected a date without a time component")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("expected an ISO date")
    return date.fromisoformat(value)


def _aware_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    else:
        raise ValueError("expected an ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


class WeatherLocation(BaseModel):
    """Warehouse-to-city-centroid mapping used for the weather proxy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warehouse_code: str = Field(pattern=r"^WH\d{2}$")
    warehouse_city: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    location_basis: Literal["warehouse_city_centroid"] = "warehouse_city_centroid"


DEFAULT_WEATHER_LOCATIONS: tuple[WeatherLocation, ...] = tuple(
    WeatherLocation(**record)
    for record in (
        {
            "warehouse_code": "WH01",
            "warehouse_city": "Mumbai",
            "latitude": 19.0760,
            "longitude": 72.8777,
        },
        {
            "warehouse_code": "WH02",
            "warehouse_city": "Pune",
            "latitude": 18.5204,
            "longitude": 73.8567,
        },
        {
            "warehouse_code": "WH03",
            "warehouse_city": "Bengaluru",
            "latitude": 12.9716,
            "longitude": 77.5946,
        },
        {
            "warehouse_code": "WH04",
            "warehouse_city": "Chennai",
            "latitude": 13.0827,
            "longitude": 80.2707,
        },
        {
            "warehouse_code": "WH05",
            "warehouse_city": "Delhi",
            "latitude": 28.6139,
            "longitude": 77.2090,
        },
        {
            "warehouse_code": "WH06",
            "warehouse_city": "Kolkata",
            "latitude": 22.5726,
            "longitude": 88.3639,
        },
        {
            "warehouse_code": "WH07",
            "warehouse_city": "Hyderabad",
            "latitude": 17.3850,
            "longitude": 78.4867,
        },
        {
            "warehouse_code": "WH08",
            "warehouse_city": "Nagpur",
            "latitude": 21.1458,
            "longitude": 79.0882,
        },
    )
)


class WeatherObservation(BaseModel):
    """One daily city-centroid weather observation for a warehouse."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warehouse_code: str = Field(pattern=r"^WH\d{2}$")
    warehouse_city: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    observation_date: date
    temperature_2m_max_c: float = Field(ge=-80, le=70)
    precipitation_sum_mm: float = Field(ge=0, le=2_000)
    timezone: Literal["Asia/Kolkata"]
    location_basis: Literal["warehouse_city_centroid"]

    @field_validator("observation_date", mode="before")
    @classmethod
    def validate_date(cls, value: object) -> date:
        return _date(value)


class HolidayObservation(BaseModel):
    """One typed Nager.Date Indian public-holiday observation."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    holiday_date: date = Field(validation_alias=AliasChoices("holiday_date", "date"))
    local_name: str = Field(min_length=1, validation_alias=AliasChoices("local_name", "localName"))
    name: str = Field(min_length=1)
    country_code: Literal["IN"] = Field(
        validation_alias=AliasChoices("country_code", "countryCode")
    )
    fixed: bool
    global_holiday: bool = Field(validation_alias=AliasChoices("global_holiday", "global"))
    counties: tuple[str, ...] | None = None
    launch_year: int | None = Field(
        default=None, validation_alias=AliasChoices("launch_year", "launchYear")
    )
    types: tuple[str, ...] = ()

    @field_validator("holiday_date", mode="before")
    @classmethod
    def validate_date(cls, value: object) -> date:
        return _date(value)

    def to_cache_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=False)


ContextSource = Literal["open_meteo_weather", "india_public_holidays"]


class ContextSyncMetadata(BaseModel):
    """Coverage, freshness, and provenance attached to a complete source cache."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_name: ContextSource
    source_url: str = Field(min_length=1)
    requested_date_from: date
    requested_date_to: date
    coverage_start: date
    coverage_end: date
    collected_at_utc: datetime
    record_count: int = Field(ge=0)
    expected_record_count: int = Field(ge=0)
    location_count: int = Field(ge=1)
    expected_location_count: int = Field(ge=1)
    request_count: int = Field(ge=1)
    retry_count: int = Field(ge=0)
    complete: Literal[True] = True

    @field_validator(
        "requested_date_from",
        "requested_date_to",
        "coverage_start",
        "coverage_end",
        mode="before",
    )
    @classmethod
    def validate_dates(cls, value: object) -> date:
        return _date(value)

    @field_validator("collected_at_utc", mode="before")
    @classmethod
    def validate_timestamp(cls, value: object) -> datetime:
        return _aware_utc(value)

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if self.requested_date_from > self.requested_date_to:
            raise ValueError("requested_date_from cannot be after requested_date_to")
        if self.coverage_start > self.coverage_end:
            raise ValueError("coverage_start cannot be after coverage_end")
        if (
            self.coverage_start > self.requested_date_from
            or self.coverage_end < self.requested_date_to
        ):
            raise ValueError("complete context metadata must cover the requested period")
        if self.record_count > self.expected_record_count:
            raise ValueError("record_count cannot exceed expected_record_count")
        if self.location_count > self.expected_location_count:
            raise ValueError("location_count cannot exceed expected_location_count")
        return self

    @property
    def row_coverage_ratio(self) -> float:
        return (
            1.0
            if self.expected_record_count == 0
            else self.record_count / self.expected_record_count
        )

    @property
    def location_coverage_ratio(self) -> float:
        return self.location_count / self.expected_location_count

    def is_stale(self, *, as_of: datetime, max_age: timedelta = timedelta(days=30)) -> bool:
        return _aware_utc(as_of) - self.collected_at_utc > max_age


class WeatherCache(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    metadata: ContextSyncMetadata
    observations: tuple[WeatherObservation, ...]

    @model_validator(mode="after")
    def validate_cache(self) -> Self:
        if self.metadata.source_name != "open_meteo_weather":
            raise ValueError("weather cache metadata has the wrong source")
        keys = [(row.warehouse_code, row.observation_date) for row in self.observations]
        if len(keys) != len(set(keys)):
            raise ValueError("weather cache contains duplicate warehouse-date rows")
        if self.metadata.record_count != len(self.observations):
            raise ValueError("weather record_count does not reconcile")
        if self.metadata.record_count != self.metadata.expected_record_count:
            raise ValueError("complete weather cache requires every expected warehouse-day")
        return self


class HolidayCache(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    metadata: ContextSyncMetadata
    observations: tuple[HolidayObservation, ...]

    @model_validator(mode="after")
    def validate_cache(self) -> Self:
        if self.metadata.source_name != "india_public_holidays":
            raise ValueError("holiday cache metadata has the wrong source")
        keys = [(row.holiday_date, row.name, row.counties) for row in self.observations]
        if len(keys) != len(set(keys)):
            raise ValueError("holiday cache contains duplicate observations")
        if self.metadata.record_count != len(self.observations):
            raise ValueError("holiday record_count does not reconcile")
        return self


class _OpenMeteoDaily(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    time: tuple[date, ...]
    temperature_2m_max: tuple[float | None, ...]
    precipitation_sum: tuple[float | None, ...]

    @field_validator("time", mode="before")
    @classmethod
    def validate_times(cls, value: object) -> object:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return value
        return [_date(item) for item in value]

    @model_validator(mode="after")
    def validate_lengths(self) -> Self:
        if not (len(self.time) == len(self.temperature_2m_max) == len(self.precipitation_sum)):
            raise ValueError("Open-Meteo daily arrays have different lengths")
        return self


class _OpenMeteoResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    latitude: float
    longitude: float
    timezone: Literal["Asia/Kolkata"]
    daily: _OpenMeteoDaily


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def load_weather_cache(path: str | Path) -> WeatherCache:
    try:
        return WeatherCache.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as error:
        raise ContextIngestionError(f"invalid weather cache at {path}: {error}") from error


def load_holiday_cache(path: str | Path) -> HolidayCache:
    try:
        return HolidayCache.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as error:
        raise ContextIngestionError(f"invalid holiday cache at {path}: {error}") from error


@dataclass(frozen=True, slots=True)
class ContextSourceOutcome:
    """Failure-isolated result for one optional source."""

    source_name: ContextSource
    complete: bool
    cache_path: Path
    record_count: int = 0
    error: str | None = None


@dataclass(slots=True)
class ContextClient:
    """HTTP client that promotes weather and holiday caches independently."""

    weather_cache_path: Path
    holiday_cache_path: Path
    weather_url: str = DEFAULT_WEATHER_URL
    holiday_url: str = DEFAULT_HOLIDAY_URL
    holiday_fallback_url: str = DEFAULT_HOLIDAY_FALLBACK_URL
    client: httpx.Client | None = None
    timeout_seconds: float = 30.0
    max_retries: int = 3
    base_backoff_seconds: float = 0.5
    sleep_fn: Callable[[float], None] = time.sleep
    random_fn: Callable[[], float] = random.random
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC)
    _owns_client: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.weather_cache_path = Path(self.weather_cache_path)
        self.holiday_cache_path = Path(self.holiday_cache_path)
        if self.timeout_seconds <= 0 or self.max_retries < 0 or self.base_backoff_seconds < 0:
            raise ValueError("context retry and timeout settings must be non-negative")
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.Client(timeout=httpx.Timeout(self.timeout_seconds))

    def close(self) -> None:
        if self._owns_client and self.client is not None:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _now(self) -> datetime:
        return _aware_utc(self.now_fn())

    def _get_json(
        self, url: str, *, params: Mapping[str, str] | None = None
    ) -> tuple[Any, int, int]:
        requests = 0
        retries = 0
        while True:
            requests += 1
            assert self.client is not None
            try:
                response = self.client.get(url, params=params, timeout=self.timeout_seconds)
                if response.status_code in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                response.raise_for_status()
                if response.status_code == 204:
                    return None, requests, retries
                return response.json(), requests, retries
            except (httpx.HTTPError, json.JSONDecodeError) as error:
                if retries >= self.max_retries:
                    raise ContextIngestionError(
                        f"{url} failed after {requests} request(s): {error}"
                    ) from error
                retries += 1
                self.sleep_fn(
                    self.base_backoff_seconds * (2 ** (retries - 1)) + self.random_fn() * 0.1
                )

    def _get_text(self, url: str) -> tuple[str, int, int]:
        requests = 0
        retries = 0
        while True:
            requests += 1
            assert self.client is not None
            try:
                response = self.client.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
                if not response.text.strip():
                    raise ContextIngestionError(f"{url} returned an empty calendar")
                return response.text, requests, retries
            except (httpx.HTTPError, ContextIngestionError) as error:
                if retries >= self.max_retries:
                    raise ContextIngestionError(
                        f"{url} failed after {requests} request(s): {error}"
                    ) from error
                retries += 1
                self.sleep_fn(
                    self.base_backoff_seconds * (2 ** (retries - 1)) + self.random_fn() * 0.1
                )

    @staticmethod
    def _public_holidays_from_ics(
        payload: str, *, date_from: date, date_to: date
    ) -> list[HolidayObservation]:
        """Parse public-holiday VEVENTs from the configured Google calendar fallback."""

        unfolded: list[str] = []
        for raw_line in payload.splitlines():
            if raw_line.startswith((" ", "\t")) and unfolded:
                unfolded[-1] += raw_line[1:]
            else:
                unfolded.append(raw_line.rstrip("\r"))
        events: list[dict[str, str]] = []
        event: dict[str, str] | None = None
        for line in unfolded:
            if line == "BEGIN:VEVENT":
                event = {}
            elif line == "END:VEVENT" and event is not None:
                events.append(event)
                event = None
            elif event is not None and ":" in line:
                key, value = line.split(":", 1)
                event[key.split(";", 1)[0]] = value

        def unescape(value: str) -> str:
            return (
                value.replace("\\n", "\n")
                .replace("\\,", ",")
                .replace("\\;", ";")
                .replace("\\\\", "\\")
            )

        rows: list[HolidayObservation] = []
        for item in events:
            raw_date = item.get("DTSTART")
            summary = unescape(item.get("SUMMARY", "")).strip()
            description = unescape(item.get("DESCRIPTION", "")).strip()
            if not raw_date or not summary:
                continue
            try:
                observed_date = datetime.strptime(raw_date[:8], "%Y%m%d").date()
            except ValueError:
                continue
            classification = description.splitlines()[0].strip().casefold()
            if not (date_from <= observed_date <= date_to) or classification != "public holiday":
                continue
            rows.append(
                HolidayObservation(
                    holiday_date=observed_date,
                    local_name=summary,
                    name=summary,
                    country_code="IN",
                    fixed=False,
                    global_holiday=True,
                    counties=None,
                    launch_year=None,
                    types=("Public",),
                )
            )
        return rows

    @staticmethod
    def _validate_range(date_from: date, date_to: date) -> None:
        if date_from > date_to:
            raise ValueError("date_from cannot be after date_to")

    def sync_weather(
        self,
        *,
        date_from: date = DEFAULT_CONTEXT_DATE_FROM,
        date_to: date = DEFAULT_CONTEXT_DATE_TO,
        locations: Sequence[WeatherLocation] = DEFAULT_WEATHER_LOCATIONS,
    ) -> WeatherCache:
        """Fetch every warehouse-day and promote only a complete weather cache."""

        self._validate_range(date_from, date_to)
        if not locations:
            raise ValueError("at least one weather location is required")
        if len({location.warehouse_code for location in locations}) != len(locations):
            raise ValueError("weather locations must have unique warehouse codes")
        rows: list[WeatherObservation] = []
        request_count = 0
        retry_count = 0
        expected_dates = tuple(
            date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)
        )
        for location in locations:
            payload, requests, retries = self._get_json(
                self.weather_url,
                params={
                    "latitude": str(location.latitude),
                    "longitude": str(location.longitude),
                    "start_date": date_from.isoformat(),
                    "end_date": date_to.isoformat(),
                    "daily": "temperature_2m_max,precipitation_sum",
                    "timezone": "Asia/Kolkata",
                },
            )
            request_count += requests
            retry_count += retries
            try:
                response = _OpenMeteoResponse.model_validate(payload)
            except (ValidationError, ValueError) as error:
                raise ContextIngestionError(
                    f"Open-Meteo schema failed for {location.warehouse_code}: {error}"
                ) from error
            if response.daily.time != expected_dates:
                raise ContextIngestionError(
                    f"Open-Meteo returned incomplete dates for {location.warehouse_code}"
                )
            for observed_date, maximum, precipitation in zip(
                response.daily.time,
                response.daily.temperature_2m_max,
                response.daily.precipitation_sum,
                strict=True,
            ):
                if maximum is None or precipitation is None:
                    raise ContextIngestionError(
                        f"Open-Meteo returned a missing daily value for {location.warehouse_code}"
                    )
                rows.append(
                    WeatherObservation(
                        warehouse_code=location.warehouse_code,
                        warehouse_city=location.warehouse_city,
                        latitude=location.latitude,
                        longitude=location.longitude,
                        observation_date=observed_date,
                        temperature_2m_max_c=maximum,
                        precipitation_sum_mm=precipitation,
                        timezone="Asia/Kolkata",
                        location_basis=location.location_basis,
                    )
                )
        expected_count = len(expected_dates) * len(locations)
        metadata = ContextSyncMetadata(
            source_name="open_meteo_weather",
            source_url=self.weather_url,
            requested_date_from=date_from,
            requested_date_to=date_to,
            coverage_start=date_from,
            coverage_end=date_to,
            collected_at_utc=self._now(),
            record_count=len(rows),
            expected_record_count=expected_count,
            location_count=len(locations),
            expected_location_count=len(locations),
            request_count=request_count,
            retry_count=retry_count,
        )
        cache = WeatherCache(
            schema_version=CONTEXT_CACHE_SCHEMA_VERSION,
            metadata=metadata,
            observations=tuple(rows),
        )
        _atomic_json(self.weather_cache_path, cache.model_dump(mode="json"))
        return cache

    def sync_holidays(
        self,
        *,
        date_from: date = DEFAULT_CONTEXT_DATE_FROM,
        date_to: date = DEFAULT_CONTEXT_DATE_TO,
    ) -> HolidayCache:
        """Fetch Indian holidays, falling back when Nager does not support country IN."""

        self._validate_range(date_from, date_to)
        rows: list[HolidayObservation] = []
        request_count = 0
        retry_count = 0
        source_url = self.holiday_url
        nager_supported = True
        for year in range(date_from.year, date_to.year + 1):
            url = f"{self.holiday_url.rstrip('/')}/{year}/IN"
            payload, requests, retries = self._get_json(url)
            request_count += requests
            retry_count += retries
            if payload is None:
                nager_supported = False
                break
            if not isinstance(payload, list):
                raise ContextIngestionError(f"Nager.Date returned a non-list payload for {year}")
            try:
                parsed = [HolidayObservation.model_validate(record) for record in payload]
            except (ValidationError, ValueError) as error:
                raise ContextIngestionError(
                    f"Nager.Date schema failed for {year}: {error}"
                ) from error
            rows.extend(row for row in parsed if date_from <= row.holiday_date <= date_to)
        if not nager_supported:
            calendar_payload, requests, retries = self._get_text(self.holiday_fallback_url)
            request_count += requests
            retry_count += retries
            rows = self._public_holidays_from_ics(
                calendar_payload,
                date_from=date_from,
                date_to=date_to,
            )
            source_url = self.holiday_fallback_url
        expected_years = set(range(date_from.year, date_to.year + 1))
        observed_years = {row.holiday_date.year for row in rows}
        if observed_years != expected_years:
            raise ContextIngestionError(
                "holiday provider did not return at least one public holiday for every "
                f"requested year: expected={sorted(expected_years)}, "
                f"observed={sorted(observed_years)}"
            )
        unique = {(row.holiday_date, row.name, row.counties): row for row in rows}
        observations = tuple(
            unique[key] for key in sorted(unique, key=lambda item: (item[0], item[1], str(item[2])))
        )
        metadata = ContextSyncMetadata(
            source_name="india_public_holidays",
            source_url=source_url,
            requested_date_from=date_from,
            requested_date_to=date_to,
            coverage_start=date_from,
            coverage_end=date_to,
            collected_at_utc=self._now(),
            record_count=len(observations),
            expected_record_count=len(observations),
            location_count=1,
            expected_location_count=1,
            request_count=request_count,
            retry_count=retry_count,
        )
        cache = HolidayCache(
            schema_version=CONTEXT_CACHE_SCHEMA_VERSION,
            metadata=metadata,
            observations=observations,
        )
        _atomic_json(self.holiday_cache_path, cache.model_dump(mode="json"))
        return cache

    def sync_all(
        self,
        *,
        date_from: date = DEFAULT_CONTEXT_DATE_FROM,
        date_to: date = DEFAULT_CONTEXT_DATE_TO,
    ) -> tuple[ContextSourceOutcome, ContextSourceOutcome]:
        """Refresh sources independently; one failure never prevents the other attempt."""

        outcomes: list[ContextSourceOutcome] = []
        refreshers: tuple[
            tuple[
                ContextSource,
                Path,
                Callable[..., WeatherCache | HolidayCache],
            ],
            ...,
        ] = (
            ("open_meteo_weather", self.weather_cache_path, self.sync_weather),
            ("india_public_holidays", self.holiday_cache_path, self.sync_holidays),
        )
        for source_name, cache_path, refresh in refreshers:
            try:
                cache = refresh(date_from=date_from, date_to=date_to)
                outcomes.append(
                    ContextSourceOutcome(
                        source_name=source_name,
                        complete=True,
                        cache_path=cache_path,
                        record_count=cache.metadata.record_count,
                    )
                )
            except Exception as error:
                outcomes.append(
                    ContextSourceOutcome(
                        source_name=source_name,
                        complete=False,
                        cache_path=cache_path,
                        error=f"{type(error).__name__}: {error}",
                    )
                )
        return outcomes[0], outcomes[1]
