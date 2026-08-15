"""Resilient ingestion for the Kestrel carrier-billing API.

The partner service is deliberately unreliable and exposes invoice amounts in paise.
This module keeps those source units for audit, exposes exact INR conversions, follows
cursor pagination to completion, and promotes a cache only after a complete cursor walk.
An interrupted walk leaves the previous last-good cache untouched and can resume from an
atomic cursor checkpoint backed by an append-only partial file.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

IST = ZoneInfo("Asia/Kolkata")
CACHE_SCHEMA_VERSION = 1
DEFAULT_PAGE_SIZE = 200


class FreightIngestionError(RuntimeError):
    """Base exception for freight ingestion failures."""


class FreightAPIError(FreightIngestionError):
    """Raised for an API or transport response that cannot be recovered."""

    def __init__(self, message: str, *, request_count: int = 0, retry_count: int = 0) -> None:
        super().__init__(message)
        self.request_count = request_count
        self.retry_count = retry_count


class FreightSchemaError(FreightAPIError):
    """Raised when the API returns JSON that violates the declared source contract."""


class FreightCacheError(FreightIngestionError):
    """Raised when a cache or checkpoint does not satisfy its schema."""


class FreightPaginationError(FreightIngestionError):
    """Raised when cursors repeat or a walk exceeds its configured page bound."""


def _parse_iso_date(value: object) -> date:
    if isinstance(value, datetime):
        raise ValueError("expected a date without a time component")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("expected an ISO date string")
    return date.fromisoformat(value)


def _parse_aware_datetime(value: object, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    else:
        raise ValueError(f"{field_name} must be an ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed


def _paise_to_inr(value: int) -> Decimal:
    return (Decimal(value) / Decimal(100)).quantize(Decimal("0.01"))


class FreightInvoice(BaseModel):
    """One validated and normalized carrier freight invoice."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    invoice_id: str = Field(min_length=1)
    carrier_id: str = Field(min_length=1)
    carrier_name: str = Field(min_length=1)
    warehouse_code: str = Field(min_length=1)
    route_code: str = Field(min_length=1)
    invoice_date: date
    service_date: date
    amount_paise: int = Field(ge=0, strict=True)
    currency: Literal["INR"]
    fuel_surcharge_pct: float = Field(ge=0)
    detention_charge_paise: int = Field(ge=0, strict=True)
    distance_km: float = Field(ge=0)
    weight_kg: float = Field(ge=0)
    temperature_controlled: bool
    status: Literal["PAID", "PENDING", "DISPUTED"]
    created_at_utc: datetime

    @model_validator(mode="before")
    @classmethod
    def normalize_source_names(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        record = dict(value)
        aliases = (
            ("amount", "amount_paise", "amount_inr"),
            ("detention_charge", "detention_charge_paise", "detention_charge_inr"),
        )
        for source_name, normalized_name, inr_name in aliases:
            source_value = record.pop(source_name, None)
            if source_value is not None:
                existing = record.get(normalized_name)
                if existing is not None and existing != source_value:
                    raise ValueError(f"conflicting {source_name} and {normalized_name}")
                record[normalized_name] = source_value
            cached_inr = record.pop(inr_name, None)
            raw_paise = record.get(normalized_name)
            if (
                cached_inr is not None
                and isinstance(raw_paise, int)
                and Decimal(str(cached_inr)) != _paise_to_inr(raw_paise)
            ):
                raise ValueError(f"cached {inr_name} does not match {normalized_name}")

        cached_ist = record.pop("created_at_ist", None)
        raw_created = record.get("created_at_utc")
        if cached_ist is not None and raw_created is not None:
            utc_value = _parse_aware_datetime(raw_created, field_name="created_at_utc")
            ist_value = _parse_aware_datetime(cached_ist, field_name="created_at_ist")
            if utc_value.astimezone(IST) != ist_value.astimezone(IST):
                raise ValueError("cached created_at_ist does not match created_at_utc")
        return record

    @field_validator("invoice_date", "service_date", mode="before")
    @classmethod
    def validate_dates(cls, value: object) -> date:
        return _parse_iso_date(value)

    @field_validator("created_at_utc", mode="before")
    @classmethod
    def normalize_created_timestamp(cls, value: object) -> datetime:
        return _parse_aware_datetime(value, field_name="created_at_utc").astimezone(UTC)

    @property
    def amount_inr(self) -> Decimal:
        """Invoice amount converted exactly once from paise to rupees."""

        return _paise_to_inr(self.amount_paise)

    @property
    def detention_charge_inr(self) -> Decimal:
        """Detention charge converted exactly once from paise to rupees."""

        return _paise_to_inr(self.detention_charge_paise)

    @property
    def created_at_ist(self) -> datetime:
        """UTC source timestamp represented in the Kestrel operating timezone."""

        return self.created_at_utc.astimezone(IST)

    def to_cache_dict(self) -> dict[str, object]:
        """Return an auditable JSON record containing raw and normalized values."""

        return {
            "invoice_id": self.invoice_id,
            "carrier_id": self.carrier_id,
            "carrier_name": self.carrier_name,
            "warehouse_code": self.warehouse_code,
            "route_code": self.route_code,
            "invoice_date": self.invoice_date.isoformat(),
            "service_date": self.service_date.isoformat(),
            "amount_paise": self.amount_paise,
            "amount_inr": format(self.amount_inr, "f"),
            "currency": self.currency,
            "fuel_surcharge_pct": self.fuel_surcharge_pct,
            "detention_charge_paise": self.detention_charge_paise,
            "detention_charge_inr": format(self.detention_charge_inr, "f"),
            "distance_km": self.distance_km,
            "weight_kg": self.weight_kg,
            "temperature_controlled": self.temperature_controlled,
            "status": self.status,
            "created_at_utc": self.created_at_utc.isoformat(),
            "created_at_ist": self.created_at_ist.isoformat(),
        }


class FreightInvoicePage(BaseModel):
    """Validated cursor page returned by ``/v1/freight_invoices``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data: tuple[FreightInvoice, ...]
    next_cursor: str | None
    page_size: int = Field(ge=0, strict=True)
    total_estimate: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def validate_page_size(self) -> Self:
        if self.page_size != len(self.data):
            raise ValueError(
                f"page_size={self.page_size} does not match {len(self.data)} data records"
            )
        if self.next_cursor is not None and not self.next_cursor.strip():
            raise ValueError("next_cursor cannot be blank")
        return self


CacheMode = Literal["full_replace", "range_upsert"]


class FreightSyncMetadata(BaseModel):
    """Explicit completeness and provenance for a freight synchronization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    complete: bool
    cache_mode: CacheMode
    source_url: str
    date_from: date | None = None
    date_to: date | None = None
    started_at_utc: datetime
    completed_at_utc: datetime | None = None
    page_count: int = Field(ge=0)
    request_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    fetched_count: int = Field(ge=0)
    record_count: int = Field(ge=0)
    next_cursor: str | None = None
    resumed: bool = False
    error: str | None = None

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def validate_optional_dates(cls, value: object) -> date | None:
        return None if value is None else _parse_iso_date(value)

    @field_validator("started_at_utc", "completed_at_utc", mode="before")
    @classmethod
    def validate_metadata_timestamps(cls, value: object) -> datetime | None:
        if value is None:
            return None
        return _parse_aware_datetime(value, field_name="metadata timestamp").astimezone(UTC)

    @model_validator(mode="after")
    def validate_completeness(self) -> Self:
        if self.complete:
            if self.completed_at_utc is None:
                raise ValueError("complete sync metadata requires completed_at_utc")
            if self.next_cursor is not None:
                raise ValueError("complete sync metadata cannot retain a next_cursor")
            if self.error is not None:
                raise ValueError("complete sync metadata cannot retain an error")
        elif self.completed_at_utc is not None:
            raise ValueError("incomplete sync metadata cannot have completed_at_utc")
        return self


class FreightCache(BaseModel):
    """Schema-validated last-good cache payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    metadata: FreightSyncMetadata
    invoices: tuple[FreightInvoice, ...]

    @model_validator(mode="after")
    def validate_last_good(self) -> Self:
        if not self.metadata.complete:
            raise ValueError("last-good cache metadata must be complete")
        if self.metadata.record_count != len(self.invoices):
            raise ValueError("cache record_count does not match its invoices array")
        invoice_ids = [invoice.invoice_id for invoice in self.invoices]
        if len(invoice_ids) != len(set(invoice_ids)):
            raise ValueError("last-good cache contains duplicate invoice_id values")
        return self


@dataclass(frozen=True, slots=True)
class FreightSyncResult:
    """Invoices collected in a run plus completeness and cache outcome."""

    invoices: tuple[FreightInvoice, ...]
    metadata: FreightSyncMetadata
    cache_path: Path
    last_good_available: bool

    @property
    def complete(self) -> bool:
        return self.metadata.complete


class _Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    source_url: str
    date_from: date | None = None
    date_to: date | None = None
    started_at_utc: datetime
    updated_at_utc: datetime
    next_cursor: str | None
    page_count: int = Field(ge=0)
    request_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    fetched_count: int = Field(ge=0)
    seen_cursors: tuple[str, ...]
    partial_file: str
    error: str | None = None
    complete: Literal[False] = False

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def validate_optional_dates(cls, value: object) -> date | None:
        return None if value is None else _parse_iso_date(value)

    @field_validator("started_at_utc", "updated_at_utc", mode="before")
    @classmethod
    def validate_timestamps(cls, value: object) -> datetime:
        return _parse_aware_datetime(value, field_name="checkpoint timestamp").astimezone(UTC)


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
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


def _deduplicate_invoices(
    invoices: Sequence[FreightInvoice],
) -> dict[str, FreightInvoice]:
    by_id: dict[str, FreightInvoice] = {}
    for invoice in invoices:
        by_id[invoice.invoice_id] = invoice
    return by_id


def _sorted_invoices(by_id: Mapping[str, FreightInvoice]) -> tuple[FreightInvoice, ...]:
    return tuple(by_id[invoice_id] for invoice_id in sorted(by_id))


def _cache_payload(
    invoices: Sequence[FreightInvoice], metadata: FreightSyncMetadata
) -> dict[str, object]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "metadata": metadata.model_dump(mode="json"),
        "invoices": [invoice.to_cache_dict() for invoice in invoices],
    }


def write_freight_cache(
    path: str | Path,
    invoices: Sequence[FreightInvoice],
    metadata: FreightSyncMetadata,
) -> None:
    """Atomically promote a complete, de-duplicated last-good cache."""

    if not metadata.complete:
        raise FreightCacheError("refusing to promote an incomplete freight sync")
    by_id = _deduplicate_invoices(invoices)
    normalized = _sorted_invoices(by_id)
    if metadata.record_count != len(normalized):
        raise FreightCacheError("sync record_count does not match de-duplicated invoices")
    _atomic_write_json(Path(path), _cache_payload(normalized, metadata))


def load_last_good_cache(path: str | Path) -> FreightCache:
    """Load and validate a complete cache without constructing or calling an API client."""

    cache_path = Path(path)
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return FreightCache.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as error:
        raise FreightCacheError(
            f"invalid freight last-good cache at {cache_path}: {error}"
        ) from error


def read_freight_cache(path: str | Path) -> FreightCache:
    """Compatibility alias for :func:`load_last_good_cache`."""

    return load_last_good_cache(path)


def load_cached_invoices(path: str | Path) -> tuple[FreightInvoice, ...]:
    """Return only invoices from a validated last-good cache."""

    return load_last_good_cache(path).invoices


def _normalize_filter(value: date | str | None, *, name: str) -> date | None:
    if value is None:
        return None
    try:
        return _parse_iso_date(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date") from error


@dataclass(slots=True)
class FreightClient:
    """Cursor client with bounded retry, checkpointing, and last-good promotion."""

    base_url: str
    api_key: str | None
    cache_path: Path
    client: httpx.Client | None = None
    timeout_seconds: float = 15.0
    max_retries: int = 5
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 8.0
    jitter_seconds: float = 0.25
    max_pages: int = 1_000
    sleep_fn: Callable[[float], None] = time.sleep
    random_fn: Callable[[], float] = random.random
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC)
    _owns_client: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.cache_path = Path(self.cache_path)
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.base_backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("backoff values cannot be negative")
        if self.base_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("base_backoff_seconds cannot exceed max_backoff_seconds")
        if self.jitter_seconds < 0:
            raise ValueError("jitter_seconds cannot be negative")
        if self.max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.Client(timeout=httpx.Timeout(self.timeout_seconds))

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/v1/freight_invoices"

    @property
    def checkpoint_path(self) -> Path:
        return self.cache_path.with_name(f".{self.cache_path.name}.checkpoint.json")

    @property
    def partial_path(self) -> Path:
        return self.cache_path.with_name(f".{self.cache_path.name}.partial.ndjson")

    def close(self) -> None:
        if self._owns_client and self.client is not None:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def load_last_good(self) -> FreightCache:
        """Load the cache without making a network request."""

        return load_last_good_cache(self.cache_path)

    def _now_utc(self) -> datetime:
        value = self.now_fn()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now_fn must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None or retry_at.utcoffset() is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at.astimezone(UTC) - self._now_utc()).total_seconds())

    def _backoff_seconds(self, retry_number: int) -> float:
        exponential = self.base_backoff_seconds * (2 ** (retry_number - 1))
        jitter = self.random_fn() * self.jitter_seconds
        return min(self.max_backoff_seconds, exponential + jitter)

    def _request_page(
        self,
        *,
        cursor: str | None,
        date_from: date | None,
        date_to: date | None,
    ) -> tuple[FreightInvoicePage, int, int]:
        if not self.api_key:
            raise FreightAPIError("freight API key is not configured")
        params: dict[str, str | int] = {"limit": DEFAULT_PAGE_SIZE}
        if cursor is not None:
            params["cursor"] = cursor
        if date_from is not None:
            params["from"] = date_from.isoformat()
        if date_to is not None:
            params["to"] = date_to.isoformat()

        request_count = 0
        retry_count = 0
        while True:
            request_count += 1
            assert self.client is not None
            try:
                response = self.client.get(
                    self.endpoint,
                    params=params,
                    headers={"X-API-Key": self.api_key},
                    timeout=self.timeout_seconds,
                )
            except httpx.HTTPError as error:
                if retry_count >= self.max_retries:
                    raise FreightAPIError(
                        f"freight request failed after {request_count} attempts: {error}",
                        request_count=request_count,
                        retry_count=retry_count,
                    ) from error
                retry_count += 1
                self.sleep_fn(self._backoff_seconds(retry_count))
                continue

            if response.status_code in (429, 503):
                if retry_count >= self.max_retries:
                    raise FreightAPIError(
                        f"freight API returned {response.status_code} after "
                        f"{request_count} attempts",
                        request_count=request_count,
                        retry_count=retry_count,
                    )
                retry_count += 1
                delay = self._backoff_seconds(retry_count)
                if response.status_code == 429:
                    retry_after = self._retry_after_seconds(response)
                    if retry_after is not None:
                        delay = max(delay, retry_after)
                self.sleep_fn(delay)
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise FreightAPIError(
                    f"freight API returned {response.status_code}",
                    request_count=request_count,
                    retry_count=retry_count,
                ) from error

            try:
                page = FreightInvoicePage.model_validate(response.json())
            except (json.JSONDecodeError, ValidationError, ValueError) as error:
                raise FreightSchemaError(
                    f"freight page failed schema validation: {error}",
                    request_count=request_count,
                    retry_count=retry_count,
                ) from error
            return page, request_count, retry_count

    def _append_partial(self, invoices: Sequence[FreightInvoice]) -> None:
        self.partial_path.parent.mkdir(parents=True, exist_ok=True)
        with self.partial_path.open("a", encoding="utf-8") as partial:
            for invoice in invoices:
                json.dump(invoice.to_cache_dict(), partial, ensure_ascii=False, sort_keys=True)
                partial.write("\n")
            partial.flush()
            os.fsync(partial.fileno())

    def _read_partial(self) -> dict[str, FreightInvoice]:
        by_id: dict[str, FreightInvoice] = {}
        try:
            with self.partial_path.open(encoding="utf-8") as partial:
                for line_number, line in enumerate(partial, start=1):
                    if not line.strip():
                        continue
                    try:
                        invoice = FreightInvoice.model_validate_json(line)
                    except (ValidationError, ValueError) as error:
                        raise FreightCacheError(
                            f"invalid partial freight file at line {line_number}: {error}"
                        ) from error
                    by_id[invoice.invoice_id] = invoice
        except OSError as error:
            raise FreightCacheError(f"cannot read partial freight file: {error}") from error
        return by_id

    def _write_checkpoint(
        self,
        *,
        source_url: str,
        date_from: date | None,
        date_to: date | None,
        started_at_utc: datetime,
        next_cursor: str | None,
        page_count: int,
        request_count: int,
        retry_count: int,
        fetched_count: int,
        seen_cursors: Sequence[str],
        error: str | None,
    ) -> None:
        checkpoint = _Checkpoint(
            schema_version=CACHE_SCHEMA_VERSION,
            source_url=source_url,
            date_from=date_from,
            date_to=date_to,
            started_at_utc=started_at_utc,
            updated_at_utc=self._now_utc(),
            next_cursor=next_cursor,
            page_count=page_count,
            request_count=request_count,
            retry_count=retry_count,
            fetched_count=fetched_count,
            seen_cursors=tuple(seen_cursors),
            partial_file=self.partial_path.name,
            error=error,
        )
        _atomic_write_json(
            self.checkpoint_path,
            checkpoint.model_dump(mode="json"),
        )

    def _load_checkpoint(
        self, *, date_from: date | None, date_to: date | None
    ) -> _Checkpoint | None:
        if not self.checkpoint_path.is_file():
            return None
        try:
            payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            checkpoint = _Checkpoint.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError, ValueError) as error:
            raise FreightCacheError(
                f"invalid freight checkpoint at {self.checkpoint_path}: {error}"
            ) from error
        if (
            checkpoint.source_url != self.endpoint
            or checkpoint.date_from != date_from
            or checkpoint.date_to != date_to
            or checkpoint.partial_file != self.partial_path.name
            or (checkpoint.page_count > 0 and not self.partial_path.is_file())
        ):
            return None
        return checkpoint

    def _reset_partial_state(self) -> None:
        self.checkpoint_path.unlink(missing_ok=True)
        self.partial_path.unlink(missing_ok=True)

    def _last_good_available(self) -> bool:
        if not self.cache_path.is_file():
            return False
        try:
            load_last_good_cache(self.cache_path)
        except FreightCacheError:
            return False
        return True

    def _existing_for_upsert(self) -> dict[str, FreightInvoice]:
        if not self.cache_path.is_file():
            return {}
        cache = load_last_good_cache(self.cache_path)
        return _deduplicate_invoices(cache.invoices)

    def sync(
        self,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        resume: bool = True,
    ) -> FreightSyncResult:
        """Walk all cursors and atomically promote only a complete result.

        A filtered walk upserts by ``invoice_id`` into any existing complete cache. An
        unfiltered walk replaces the cache, preventing stale source records from surviving
        a full refresh. Any failure returns ``complete=False`` metadata, persists a cursor
        checkpoint, and leaves the last-good cache unchanged.
        """

        normalized_from = _normalize_filter(date_from, name="date_from")
        normalized_to = _normalize_filter(date_to, name="date_to")
        if normalized_from and normalized_to and normalized_from > normalized_to:
            raise ValueError("date_from cannot be after date_to")

        cache_mode: CacheMode = (
            "full_replace"
            if normalized_from is None and normalized_to is None
            else "range_upsert"
        )
        checkpoint = (
            self._load_checkpoint(date_from=normalized_from, date_to=normalized_to)
            if resume
            else None
        )
        resumed = checkpoint is not None
        if checkpoint is None:
            self._reset_partial_state()
            started_at = self._now_utc()
            cursor: str | None = None
            page_count = 0
            request_count = 0
            retry_count = 0
            fetched_count = 0
            seen_cursors: list[str] = []
            partial_by_id: dict[str, FreightInvoice] = {}
        else:
            started_at = checkpoint.started_at_utc
            cursor = checkpoint.next_cursor
            page_count = checkpoint.page_count
            request_count = checkpoint.request_count
            retry_count = checkpoint.retry_count
            fetched_count = checkpoint.fetched_count
            seen_cursors = list(checkpoint.seen_cursors)
            partial_by_id = self._read_partial() if self.partial_path.is_file() else {}

        try:
            walk_finished = resumed and page_count > 0 and cursor is None
            while not walk_finished:
                if page_count >= self.max_pages:
                    raise FreightPaginationError(
                        f"freight cursor walk exceeded {self.max_pages} pages"
                    )
                if cursor is not None and cursor in seen_cursors:
                    raise FreightPaginationError(f"freight cursor repeated: {cursor}")
                requested_cursor = cursor

                try:
                    page, page_requests, page_retries = self._request_page(
                        cursor=cursor,
                        date_from=normalized_from,
                        date_to=normalized_to,
                    )
                except FreightAPIError as error:
                    request_count += error.request_count
                    retry_count += error.retry_count
                    raise
                request_count += page_requests
                retry_count += page_retries
                if requested_cursor is not None:
                    seen_cursors.append(requested_cursor)

                for invoice in page.data:
                    if normalized_from and invoice.invoice_date < normalized_from:
                        raise FreightSchemaError(
                            f"invoice {invoice.invoice_id} is before requested date_from",
                            request_count=0,
                            retry_count=0,
                        )
                    if normalized_to and invoice.invoice_date > normalized_to:
                        raise FreightSchemaError(
                            f"invoice {invoice.invoice_id} is after requested date_to",
                            request_count=0,
                            retry_count=0,
                        )

                self._append_partial(page.data)
                for invoice in page.data:
                    partial_by_id[invoice.invoice_id] = invoice
                page_count += 1
                fetched_count += len(page.data)
                cursor = page.next_cursor
                self._write_checkpoint(
                    source_url=self.endpoint,
                    date_from=normalized_from,
                    date_to=normalized_to,
                    started_at_utc=started_at,
                    next_cursor=cursor,
                    page_count=page_count,
                    request_count=request_count,
                    retry_count=retry_count,
                    fetched_count=fetched_count,
                    seen_cursors=seen_cursors,
                    error=None,
                )
                if cursor is None:
                    walk_finished = True

            final_by_id = (
                self._existing_for_upsert() if cache_mode == "range_upsert" else {}
            )
            final_by_id.update(partial_by_id)
            final_invoices = _sorted_invoices(final_by_id)
            completed_at = self._now_utc()
            metadata = FreightSyncMetadata(
                complete=True,
                cache_mode=cache_mode,
                source_url=self.endpoint,
                date_from=normalized_from,
                date_to=normalized_to,
                started_at_utc=started_at,
                completed_at_utc=completed_at,
                page_count=page_count,
                request_count=request_count,
                retry_count=retry_count,
                fetched_count=fetched_count,
                record_count=len(final_invoices),
                next_cursor=None,
                resumed=resumed,
                error=None,
            )
            write_freight_cache(self.cache_path, final_invoices, metadata)
            self._reset_partial_state()
            return FreightSyncResult(
                invoices=final_invoices,
                metadata=metadata,
                cache_path=self.cache_path,
                last_good_available=True,
            )
        except (FreightIngestionError, OSError, ValueError) as error:
            self._write_checkpoint(
                source_url=self.endpoint,
                date_from=normalized_from,
                date_to=normalized_to,
                started_at_utc=started_at,
                next_cursor=cursor,
                page_count=page_count,
                request_count=request_count,
                retry_count=retry_count,
                fetched_count=fetched_count,
                seen_cursors=seen_cursors,
                error=f"{type(error).__name__}: {error}",
            )
            metadata = FreightSyncMetadata(
                complete=False,
                cache_mode=cache_mode,
                source_url=self.endpoint,
                date_from=normalized_from,
                date_to=normalized_to,
                started_at_utc=started_at,
                completed_at_utc=None,
                page_count=page_count,
                request_count=request_count,
                retry_count=retry_count,
                fetched_count=fetched_count,
                record_count=len(partial_by_id),
                next_cursor=cursor,
                resumed=resumed,
                error=f"{type(error).__name__}: {error}",
            )
            return FreightSyncResult(
                invoices=_sorted_invoices(partial_by_id),
                metadata=metadata,
                cache_path=self.cache_path,
                last_good_available=self._last_good_available(),
            )


FreightAPIClient = FreightClient
