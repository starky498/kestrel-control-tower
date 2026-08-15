"""Governed natural-language routing over the deterministic metric service.

This module deliberately contains no SQL and accepts no SQL-shaped output from a model.
Questions are reduced to an allow-listed, typed intent and dispatched only to public
``AnalyticsService`` methods. The parser is deterministic so the same question and data
anchor always produce the same interpretation.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast

import pandas as pd

from kestrel.metrics.periods import (
    Period,
    last_complete_month,
    latest_complete_fiscal_quarter,
)
from kestrel.metrics.service import FilterSet, MetricValue, QuantityBasis


class MetricKey(StrEnum):
    """Metrics the question layer is permitted to request."""

    FILL_RATE = "fill_rate"
    STRICT_OTIF = "strict_otif"
    RETURNS = "returns"
    CHILLED_EXCURSIONS = "chilled_excursions"
    LATE_ROUTES = "late_routes"
    MARKET_PRICE_GAP = "market_price_gap"
    FREIGHT_PER_CASE = "freight_per_case"
    DISCONTINUED_SKUS = "discontinued_skus"


class DimensionKey(StrEnum):
    """Dimensions the parser may expose to metric methods."""

    CUSTOMER_REGION = "customer_region"
    WAREHOUSE_REGION = "warehouse_region"
    WAREHOUSE = "warehouse"
    ROUTE = "route"
    OUTLET = "outlet"
    CHANNEL = "channel"
    CATEGORY = "category"
    SKU = "sku"
    RETURN_REASON = "reason"
    MONTH = "month"


class Ranking(StrEnum):
    NONE = "none"
    WORST = "worst"
    BEST = "best"


class ParseStatus(StrEnum):
    READY = "ready"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class AnswerStatus(StrEnum):
    OK = "ok"
    NO_DATA = "no_data"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    INTEGRATION_REQUIRED = "integration_required"
    ERROR = "error"


@dataclass(frozen=True)
class IntentFilters:
    """Allow-listed filters parsed from a question.

    City and category are retained for integrations that are not yet part of
    ``AnalyticsService``. They are never interpolated into a query by this layer.
    """

    customer_regions: tuple[str, ...] = ()
    warehouse_regions: tuple[str, ...] = ()
    warehouse_codes: tuple[str, ...] = ()
    route_codes: tuple[str, ...] = ()
    outlet_codes: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()

    def to_metric_filters(self, period: Period) -> FilterSet:
        return FilterSet(
            start_date=period.start,
            end_date=period.end,
            customer_regions=self.customer_regions,
            warehouse_regions=self.warehouse_regions,
            warehouse_codes=self.warehouse_codes,
            route_codes=self.route_codes,
            outlet_codes=self.outlet_codes,
            channels=self.channels,
        )

    def labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        for name, values in (
            ("customer region", self.customer_regions),
            ("DC region", self.warehouse_regions),
            ("warehouse", self.warehouse_codes),
            ("route", self.route_codes),
            ("outlet", self.outlet_codes),
            ("channel", self.channels),
            ("city", self.cities),
            ("category", self.categories),
        ):
            if values:
                labels.append(f"{name}={', '.join(values)}")
        return tuple(labels)


@dataclass(frozen=True)
class QuestionIntent:
    """Typed interpretation passed to the allow-listed dispatcher."""

    raw_question: str
    metric: MetricKey
    dimensions: tuple[DimensionKey, ...]
    period: Period
    filters: IntentFilters
    quantity_basis: QuantityBasis = QuantityBasis.EACHES
    ranking: Ranking = Ranking.NONE
    limit: int | None = None
    late_threshold_minutes: int = 120
    rate_threshold: float = 0.10
    explain_change: bool = False

    def interpretation(self) -> str:
        dimensions = ", ".join(d.value for d in self.dimensions) or "overall"
        filters = "; ".join(self.filters.labels()) or "none"
        details = [
            f"metric={self.metric.value}",
            f"dimensions={dimensions}",
            f"period={self.period.label} ({self.period.start.isoformat()} to "
            f"{self.period.end.isoformat()})",
            f"filters={filters}",
        ]
        if self.metric in {MetricKey.FILL_RATE, MetricKey.STRICT_OTIF}:
            details.append(f"quantity basis={self.quantity_basis.value}")
        if self.limit is not None:
            details.append(f"limit={self.limit}")
        if self.metric == MetricKey.LATE_ROUTES:
            details.append(
                f"threshold=delay > {self.late_threshold_minutes} minutes on "
                f"> {self.rate_threshold:.0%} of deliveries"
            )
        if self.explain_change:
            details.append("analysis=period-over-period measured contribution")
        return "; ".join(details)


@dataclass(frozen=True)
class ParsedQuestion:
    status: ParseStatus
    intent: QuestionIntent | None
    message: str
    candidates: tuple[MetricKey, ...] = ()


@dataclass(frozen=True)
class EvidenceBlock:
    title: str
    source: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]

    @property
    def is_empty(self) -> bool:
        return not self.rows


@dataclass(frozen=True)
class QuestionAnswer:
    status: AnswerStatus
    summary: str
    interpretation: str
    definition: str
    sources: tuple[str, ...]
    evidence: tuple[EvidenceBlock, ...] = ()
    warnings: tuple[str, ...] = ()
    intent: QuestionIntent | None = None
    suggestions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation for a UI or API boundary."""

        payload = asdict(self)
        payload["status"] = self.status.value
        if self.intent is not None:
            payload["intent"]["metric"] = self.intent.metric.value  # type: ignore[index]
            payload["intent"]["dimensions"] = [  # type: ignore[index]
                dimension.value for dimension in self.intent.dimensions
            ]
            payload["intent"]["quantity_basis"] = self.intent.quantity_basis.value  # type: ignore[index]
            payload["intent"]["ranking"] = self.intent.ranking.value  # type: ignore[index]
            payload["intent"]["period"] = {  # type: ignore[index]
                "start": self.intent.period.start.isoformat(),
                "end": self.intent.period.end.isoformat(),
                "label": self.intent.period.label,
            }
        return payload


class MetricService(Protocol):
    """Narrow service boundary; it exposes metric methods, never raw query execution."""

    def available_date_range(self) -> tuple[date, date]: ...

    def filter_options(self) -> dict[str, list[str]]: ...

    def executive_summary(
        self, filters: FilterSet, basis: QuantityBasis = QuantityBasis.EACHES
    ) -> dict[str, MetricValue]: ...

    def service_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int | None = None,
        worst_first: bool = True,
    ) -> pd.DataFrame: ...

    def service_trend(
        self, filters: FilterSet, basis: QuantityBasis = QuantityBasis.EACHES
    ) -> pd.DataFrame: ...

    def cold_chain_by_dimension(
        self, filters: FilterSet, dimension: str, *, limit: int = 20
    ) -> pd.DataFrame: ...

    def returns_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        statuses: tuple[str, ...] = ("APPROVED",),
        limit: int = 20,
    ) -> pd.DataFrame: ...

    def discontinued_order_evidence(
        self, filters: FilterSet, *, limit: int = 100
    ) -> pd.DataFrame: ...

    def shortage_contributors(
        self, filters: FilterSet, dimension: str, *, limit: int = 20
    ) -> pd.DataFrame: ...


class ExternalMetricService(Protocol):
    """Optional, fixed-query metrics over governed external snapshots."""

    def freight_by_warehouse(self, filters: FilterSet) -> pd.DataFrame: ...

    def competitor_price_gap(
        self,
        filters: FilterSet,
        *,
        city: str = "Mumbai",
        category: str | None = None,
        top_n: int = 20,
    ) -> pd.DataFrame: ...


@dataclass(frozen=True)
class _MetricSpec:
    definition: str
    sources: tuple[str, ...]


_SPECS: dict[MetricKey, _MetricSpec] = {
    MetricKey.FILL_RATE: _MetricSpec(
        "Ratio of sums: delivered normalized quantity divided by ordered normalized "
        "quantity for eligible delivered/partial orders.",
        ("fct_order_service", "orders", "order_lines"),
    ),
    MetricKey.STRICT_OTIF: _MetricSpec(
        "Eligible orders where parsed actual arrival is on or before planned arrival and "
        "every line has delivered eaches >= ordered eaches, divided by eligible orders.",
        ("fct_order_service", "deliveries", "order_lines"),
    ),
    MetricKey.RETURNS: _MetricSpec(
        "Approved credit-note value and normalized absolute return quantity, grouped by "
        "the requested dimension; pending and rejected notes are excluded.",
        ("fct_return_credit_note", "returns_credit_notes", "products"),
    ),
    MetricKey.CHILLED_EXCURSIONS: _MetricSpec(
        "Distinct chilled deliveries with a temperature-excursion flag divided by all "
        "distinct chilled deliveries, multiplied by 100.",
        ("fct_delivery", "deliveries", "order_lines", "products"),
    ),
    MetricKey.LATE_ROUTES: _MetricSpec(
        "Routes where timestamp-derived delay is greater than 120 minutes on more than "
        "10% of eligible deliveries.",
        ("fct_order_service", "deliveries", "routes"),
    ),
    MetricKey.MARKET_PRICE_GAP: _MetricSpec(
        "Kestrel MRP minus the lowest latest-observed in-stock competitor shelf price "
        "for a high-confidence city/SKU match.",
        ("products", "product_price_history", "BazaarPulse observations"),
    ),
    MetricKey.FREIGHT_PER_CASE: _MetricSpec(
        "Carrier billed freight in INR divided by delivered case-equivalents for the same "
        "service period and independently reconciled dimension.",
        ("partner freight invoices", "fct_order_line", "warehouses", "routes"),
    ),
    MetricKey.DISCONTINUED_SKUS: _MetricSpec(
        "Order lines whose order date is later than the SKU discontinued date.",
        ("fct_order_line", "orders", "products", "outlets"),
    ),
}


_METRIC_PATTERNS: dict[MetricKey, tuple[str, ...]] = {
    MetricKey.FILL_RATE: (r"\bfill[ -]?rate\b",),
    MetricKey.STRICT_OTIF: (r"\botif\b", r"\bon[ -]?time[ -]?in[ -]?full\b"),
    MetricKey.RETURNS: (r"\breturns?\b", r"\bcredit[ -]?notes?\b"),
    MetricKey.CHILLED_EXCURSIONS: (
        r"\btemperature[ -]?excursions?\b",
        r"\bcold[ -]?chain\b",
    ),
    MetricKey.LATE_ROUTES: (
        r"\broutes?\b.*\b(?:late|delay(?:ed|s)?)\b",
        r"\b(?:late|delay(?:ed|s)?)\b.*\broutes?\b",
    ),
    MetricKey.MARKET_PRICE_GAP: (
        r"\bcompetitor(?:'s)?\b",
        r"\bmarket[ -]?price\b",
        r"\bprice[ -]?gaps?\b",
        r"\bmrp\b.*\bprice\b",
    ),
    MetricKey.FREIGHT_PER_CASE: (r"\bfreight\b",),
    MetricKey.DISCONTINUED_SKUS: (r"\bdiscontinued\b",),
}


_ALLOWED_DIMENSIONS: dict[MetricKey, frozenset[DimensionKey]] = {
    MetricKey.FILL_RATE: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.MONTH,
        }
    ),
    MetricKey.STRICT_OTIF: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.MONTH,
        }
    ),
    MetricKey.RETURNS: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CATEGORY,
            DimensionKey.SKU,
            DimensionKey.RETURN_REASON,
        }
    ),
    MetricKey.CHILLED_EXCURSIONS: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.MONTH,
        }
    ),
    MetricKey.LATE_ROUTES: frozenset({DimensionKey.ROUTE}),
    MetricKey.MARKET_PRICE_GAP: frozenset({DimensionKey.SKU, DimensionKey.CATEGORY}),
    MetricKey.FREIGHT_PER_CASE: frozenset({DimensionKey.WAREHOUSE, DimensionKey.ROUTE}),
    MetricKey.DISCONTINUED_SKUS: frozenset(
        {DimensionKey.OUTLET, DimensionKey.SKU, DimensionKey.WAREHOUSE}
    ),
}


_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twenty": 20,
    "fifty": 50,
    "hundred": 100,
}

_MONTHS = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): index for index, name in enumerate(calendar.month_abbr) if name})
_CITY_NAMES = ("Mumbai", "Delhi", "Bengaluru", "Chennai")


def _coerce_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _month_period(year: int, month: int, *, label: str | None = None) -> Period:
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return Period(start, end, label or start.strftime("%B %Y"))


def _fiscal_period(start_year: int, quarter: int) -> Period:
    month = 4 + (quarter - 1) * 3
    year = start_year
    if month > 12:
        month -= 12
        year += 1
    start = date(year, month, 1)
    end_month_index = year * 12 + month - 1 + 3
    end_year, end_month_zero = divmod(end_month_index, 12)
    end = date(end_year, end_month_zero + 1, 1) - timedelta(days=1)
    return Period(start, end, f"FY {start_year}-{str(start_year + 1)[-2:]} Q{quarter}")


def _expand_year(raw: str) -> int:
    value = int(raw)
    return 2000 + value if value < 100 else value


def _validate_period(
    period: Period, minimum: date, maximum: date
) -> tuple[Period | None, str | None]:
    if period.start > period.end:
        return None, "The interpreted start date is after the end date."
    if period.end < minimum or period.start > maximum:
        return (
            None,
            f"The requested period is outside the available range "
            f"{minimum.isoformat()} to {maximum.isoformat()}.",
        )
    if period.start < minimum or period.end > maximum:
        return (
            None,
            f"The requested period is only partly available. Available dates are "
            f"{minimum.isoformat()} to {maximum.isoformat()}; please give an in-range period.",
        )
    return period, None


def _resolve_period(text: str, minimum: date, maximum: date) -> tuple[Period | None, str | None]:
    iso_tokens = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if iso_tokens:
        try:
            parsed_dates = tuple(date.fromisoformat(token) for token in iso_tokens[:2])
        except ValueError:
            return None, "One of the ISO dates is invalid. Use YYYY-MM-DD."
        start = parsed_dates[0]
        end = parsed_dates[-1]
        period = Period(start, end, f"{start.isoformat()} to {end.isoformat()}")
        return _validate_period(period, minimum, maximum)

    fiscal_match = re.search(r"\bfy\s*(\d{2,4})\s*[-/]\s*(\d{2,4})\s*q([1-4])\b", text)
    if fiscal_match:
        start_year = _expand_year(fiscal_match.group(1))
        expected_end = start_year + 1
        stated_end = _expand_year(fiscal_match.group(2))
        if stated_end != expected_end:
            return None, "The fiscal-year label is inconsistent; expected consecutive years."
        return _validate_period(
            _fiscal_period(start_year, int(fiscal_match.group(3))), minimum, maximum
        )

    if re.search(r"\b(?:last|latest)(?: complete)? (?:fiscal )?quarter\b", text):
        return _validate_period(latest_complete_fiscal_quarter(maximum), minimum, maximum)

    quarter_match = re.search(r"\bq([1-4])\b", text)
    if quarter_match:
        quarter = int(quarter_match.group(1))
        candidates = [
            _fiscal_period(year, quarter) for year in range(maximum.year, minimum.year - 2, -1)
        ]
        completed = [candidate for candidate in candidates if candidate.end <= maximum]
        if not completed:
            return None, f"No complete Q{quarter} exists in the available data."
        return _validate_period(max(completed, key=lambda item: item.end), minimum, maximum)

    if re.search(r"\b(?:last|latest)(?: complete)? month\b", text):
        return _validate_period(last_complete_month(maximum), minimum, maximum)

    if re.search(r"\b(?:last|latest)(?: complete)? week\b", text):
        current_week_start = maximum - timedelta(days=maximum.weekday())
        end = maximum if maximum.weekday() == 6 else current_week_start - timedelta(days=1)
        start = end - timedelta(days=6)
        return _validate_period(
            Period(start, end, f"Week {start.isoformat()} to {end.isoformat()}"),
            minimum,
            maximum,
        )

    days_match = re.search(r"\blast\s+(\d{1,3})\s+days?\b", text)
    if days_match:
        days = int(days_match.group(1))
        if days < 1 or days > 366:
            return None, "A relative day window must be between 1 and 366 days."
        start = maximum - timedelta(days=days - 1)
        return _validate_period(
            Period(start, maximum, f"Last {days} available days"), minimum, maximum
        )

    if re.search(r"\b(?:all time|all available|full history)\b", text):
        return Period(minimum, maximum, "All available data"), None

    if re.search(r"\b(?:latest day|today)\b", text):
        return Period(maximum, maximum, f"Latest available day ({maximum.isoformat()})"), None

    month_pattern = "|".join(sorted(_MONTHS, key=len, reverse=True))
    month_match = re.search(rf"\b({month_pattern})\b(?:\s+(20\d{{2}}))?", text)
    if month_match:
        month = _MONTHS[month_match.group(1)]
        year = int(month_match.group(2)) if month_match.group(2) else maximum.year
        period = _month_period(year, month)
        if month_match.group(2) is None and period.start > maximum:
            period = _month_period(year - 1, month)
        return _validate_period(period, minimum, maximum)

    default = latest_complete_fiscal_quarter(maximum)
    return _validate_period(default, minimum, maximum)


def _detect_metrics(text: str) -> tuple[MetricKey, ...]:
    matches = []
    for metric, patterns in _METRIC_PATTERNS.items():
        if any(re.search(pattern, text) for pattern in patterns):
            matches.append(metric)
    return tuple(matches)


def _detect_dimensions(text: str) -> tuple[DimensionKey, ...]:
    dimensions: list[DimensionKey] = []
    warehouse_region = bool(re.search(r"\b(?:warehouse|dc|distribution centre) regions?\b", text))
    if warehouse_region:
        dimensions.append(DimensionKey.WAREHOUSE_REGION)
    if re.search(r"\bby (?:customer |sales )?regions?\b", text) or re.search(
        r"\bcustomer regions?\b", text
    ):
        dimensions.append(DimensionKey.CUSTOMER_REGION)
    if not warehouse_region and re.search(
        r"\b(?:(?:by|per|across)\s+|which(?:\s+\w+)?\s+)"
        r"(?:dc|dcs|warehouse|warehouses|distribution centres?)\b",
        text,
    ):
        dimensions.append(DimensionKey.WAREHOUSE)
    if re.search(r"\b(?:by|per|across)\s+routes?\b", text):
        dimensions.append(DimensionKey.ROUTE)
    if re.search(
        r"\b(?:by|per|across)\s+(?:outlets?|customers?)(?!\s+regions?\b)", text
    ) or re.search(r"\bwhich(?:\s+\w+){0,2}\s+(?:outlets?|customers?)\b", text):
        dimensions.append(DimensionKey.OUTLET)
    if re.search(r"\b(?:by|per|across)\s+channels?\b", text):
        dimensions.append(DimensionKey.CHANNEL)
    if re.search(r"\b(?:by|per|across|which)\s+(?:categories|category)\b", text):
        dimensions.append(DimensionKey.CATEGORY)
    if re.search(r"\b(?:reason|reasons|reason code|reason codes)\b", text):
        dimensions.append(DimensionKey.RETURN_REASON)
    if re.search(r"\b(?:by|per)\s+months?\b|\bmonthly\b", text):
        dimensions.append(DimensionKey.MONTH)
    if re.search(r"\b(?:by|per|top\s+\w+|which)\s+(?:skus?|products?)\b", text):
        dimensions.append(DimensionKey.SKU)
    return tuple(dict.fromkeys(dimensions))


def _default_dimensions(
    metric: MetricKey, detected: tuple[DimensionKey, ...]
) -> tuple[DimensionKey, ...]:
    if metric == MetricKey.DISCONTINUED_SKUS:
        return tuple(dict.fromkeys((*detected, DimensionKey.OUTLET, DimensionKey.SKU)))
    if metric == MetricKey.LATE_ROUTES:
        return (DimensionKey.ROUTE,)
    if detected:
        return detected
    defaults = {
        MetricKey.RETURNS: (DimensionKey.CATEGORY,),
        MetricKey.MARKET_PRICE_GAP: (DimensionKey.SKU,),
        MetricKey.FREIGHT_PER_CASE: (DimensionKey.WAREHOUSE,),
    }
    return defaults.get(metric, ())


def _extract_limit(text: str, metric: MetricKey) -> int | None:
    match = re.search(
        r"\b(?:top|bottom|lowest|highest|worst|best|which)\s+"
        r"(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|hundred)\b",
        text,
    )
    if match:
        raw = match.group(1)
        limit = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        return min(max(limit, 1), 100)
    defaults = {
        MetricKey.MARKET_PRICE_GAP: 20,
        MetricKey.DISCONTINUED_SKUS: 100,
    }
    return defaults.get(metric)


def _extract_ranking(text: str) -> Ranking:
    if re.search(r"\b(?:lowest|worst|bottom)\b", text):
        return Ranking.WORST
    if re.search(r"\b(?:highest|best|top|largest)\b", text):
        return Ranking.BEST
    return Ranking.NONE


def _extract_quantity_basis(
    text: str, metric: MetricKey
) -> tuple[QuantityBasis | None, str | None]:
    if metric not in {MetricKey.FILL_RATE, MetricKey.STRICT_OTIF}:
        return QuantityBasis.EACHES, None
    mentions_eaches = bool(re.search(r"\b(?:eaches|units?)\b", text))
    mentions_cases = bool(re.search(r"\b(?:cases?|case equivalents?)\b", text))
    if mentions_eaches and mentions_cases:
        return None, "The question requests both cases and eaches; choose one quantity basis."
    if mentions_cases:
        return QuantityBasis.CASE_EQUIVALENTS, None
    return QuantityBasis.EACHES, None


def _casefold_lookup(options: list[str]) -> dict[str, str]:
    return {option.casefold(): option for option in options}


def _matching_options(text: str, options: list[str]) -> tuple[str, ...]:
    matches = [
        canonical
        for folded, canonical in _casefold_lookup(options).items()
        if re.search(rf"(?<!\w){re.escape(folded)}(?!\w)", text)
    ]
    return tuple(dict.fromkeys(matches))


def _extract_filters(text: str, options: dict[str, list[str]]) -> tuple[IntentFilters, str | None]:
    warehouse_region_context = bool(
        re.search(r"\b(?:warehouse|dc|distribution centre) regions?\b", text)
    )
    customer_regions: tuple[str, ...] = ()
    warehouse_regions: tuple[str, ...] = ()
    region_options = options.get("customer_regions", [])
    matched_regions = _matching_options(text, region_options)
    if warehouse_region_context:
        warehouse_regions = _matching_options(
            text, options.get("warehouse_regions", region_options)
        )
    else:
        customer_regions = matched_regions

    unknown_region = re.search(r"\b(?:in|for)\s+([a-z][a-z -]+?)\s+region\b", text)
    if unknown_region and not (customer_regions or warehouse_regions):
        candidate = unknown_region.group(1).strip()
        if candidate not in {"customer", "sales", "warehouse", "dc"}:
            return IntentFilters(), f"Unknown region '{candidate.title()}'."

    warehouse_codes = tuple(
        dict.fromkeys(code.upper() for code in re.findall(r"\bwh\d{2}\b", text))
    )
    route_codes = tuple(dict.fromkeys(code.upper() for code in re.findall(r"\brt\d{4}\b", text)))
    outlet_codes = tuple(
        dict.fromkeys(code.upper() for code in re.findall(r"\b(?:out\d{5}|tst\d{5})\b", text))
    )
    valid_warehouses = set(options.get("warehouse_codes", ()))
    valid_routes = set(options.get("route_codes", ()))
    valid_outlets = set(options.get("outlet_codes", ()))
    unknown_codes = [
        code
        for code, valid in (
            *((code, valid_warehouses) for code in warehouse_codes),
            *((code, valid_routes) for code in route_codes),
            *((code, valid_outlets) for code in outlet_codes),
        )
        if valid and code not in valid
    ]
    if unknown_codes:
        return IntentFilters(), f"Unknown filter code(s): {', '.join(unknown_codes)}."

    channels = list(_matching_options(text, options.get("channels", [])))
    channel_aliases = {
        "modern trade": "MT",
        "general trade": "GT",
        "horeca": "HORECA",
        "dark store": "ECOM_DARKSTORE",
        "e-commerce": "ECOM_DARKSTORE",
        "ecommerce": "ECOM_DARKSTORE",
    }
    valid_channels = set(options.get("channels", []))
    for phrase, value in channel_aliases.items():
        if phrase in text and (not valid_channels or value in valid_channels):
            channels.append(value)

    cities = tuple(city for city in _CITY_NAMES if city.casefold() in text)
    return (
        IntentFilters(
            customer_regions=customer_regions,
            warehouse_regions=warehouse_regions,
            warehouse_codes=warehouse_codes,
            route_codes=route_codes,
            outlet_codes=outlet_codes,
            channels=tuple(dict.fromkeys(channels)),
            cities=cities,
        ),
        None,
    )


def _frame_evidence(title: str, source: str, frame: pd.DataFrame) -> EvidenceBlock:
    columns = tuple(str(column) for column in frame.columns)
    rows = tuple(
        tuple(_python_scalar(value) for value in row) for row in frame.itertuples(False, None)
    )
    return EvidenceBlock(title=title, source=source, columns=columns, rows=rows)


def _python_scalar(value: Any) -> object:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date() if value.time() == datetime.min.time() else value.to_pydatetime()
    if hasattr(value, "item"):
        value = value.item()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _monthly_periods(period: Period) -> tuple[Period, ...]:
    periods: list[Period] = []
    cursor = period.start.replace(day=1)
    while cursor <= period.end:
        month_end = date(
            cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1]
        )
        start = max(cursor, period.start)
        end = min(month_end, period.end)
        periods.append(Period(start, end, cursor.strftime("%B %Y")))
        cursor = month_end + timedelta(days=1)
    return tuple(periods)


def _format_percent(value: object) -> str:
    return "not available" if value is None else f"{_as_float(value):.1%}"


def _format_number(value: object) -> str:
    return "not available" if value is None else f"{_as_float(value):,.2f}"


def _as_float(value: object) -> float:
    return float(cast(Any, value))


class QuestionRouter:
    """Parse and answer allow-listed operational questions through ``MetricService``."""

    def __init__(
        self,
        metric_service: MetricService,
        external_service: ExternalMetricService | None = None,
    ) -> None:
        self.metric_service = metric_service
        self.external_service = external_service

    def parse(self, question: str) -> ParsedQuestion:
        stripped = question.strip()
        if not stripped:
            return ParsedQuestion(
                ParseStatus.UNSUPPORTED,
                None,
                "Enter a supply-chain question.",
            )
        text = re.sub(r"\s+", " ", stripped.casefold().replace("–", "-").replace("—", "-"))
        metrics = _detect_metrics(text)
        if not metrics:
            return ParsedQuestion(
                ParseStatus.UNSUPPORTED,
                None,
                "I can answer governed questions about fill rate, OTIF, returns, chilled "
                "excursions, late routes, market price gaps, freight, or discontinued SKUs.",
            )
        if len(metrics) > 1:
            return ParsedQuestion(
                ParseStatus.AMBIGUOUS,
                None,
                "The question names more than one metric. Ask one metric at a time so the "
                "period and denominator are explicit.",
                metrics,
            )

        metric = metrics[0]
        basis, basis_error = _extract_quantity_basis(text, metric)
        if basis_error or basis is None:
            return ParsedQuestion(ParseStatus.AMBIGUOUS, None, basis_error or "Choose a basis.")

        minimum_raw, maximum_raw = self.metric_service.available_date_range()
        minimum, maximum = _coerce_date(minimum_raw), _coerce_date(maximum_raw)
        period, period_error = _resolve_period(text, minimum, maximum)
        if period_error or period is None:
            return ParsedQuestion(
                ParseStatus.AMBIGUOUS,
                None,
                period_error or "The period could not be interpreted.",
                (metric,),
            )

        options = self.metric_service.filter_options()
        filters, filter_error = _extract_filters(text, options)
        if filter_error:
            return ParsedQuestion(ParseStatus.AMBIGUOUS, None, filter_error, (metric,))

        dimensions = _default_dimensions(metric, _detect_dimensions(text))
        unsupported_dimensions = tuple(
            dimension for dimension in dimensions if dimension not in _ALLOWED_DIMENSIONS[metric]
        )
        if unsupported_dimensions:
            names = ", ".join(dimension.value for dimension in unsupported_dimensions)
            return ParsedQuestion(
                ParseStatus.AMBIGUOUS,
                None,
                f"{metric.value} does not support dimension(s): {names}.",
                (metric,),
            )

        intent = QuestionIntent(
            raw_question=stripped,
            metric=metric,
            dimensions=dimensions,
            period=period,
            filters=filters,
            quantity_basis=basis,
            ranking=_extract_ranking(text),
            limit=_extract_limit(text, metric),
            explain_change=(
                metric == MetricKey.FILL_RATE
                and bool(
                    re.search(r"\bwhy\b", text)
                    or re.search(r"\b(?:dropped|changed|declined)\b", text)
                )
            ),
        )
        return ParsedQuestion(ParseStatus.READY, intent, "Question interpreted.")

    def answer(self, question: str) -> QuestionAnswer:
        parsed = self.parse(question)
        if parsed.status != ParseStatus.READY or parsed.intent is None:
            return self._parse_failure(parsed)

        return self.answer_intent(parsed.intent)

    def answer_intent(self, intent: QuestionIntent) -> QuestionAnswer:
        """Execute an already validated intent through allow-listed service methods."""

        spec = _SPECS[intent.metric]
        if (
            intent.metric in {MetricKey.MARKET_PRICE_GAP, MetricKey.FREIGHT_PER_CASE}
            and self.external_service is None
        ):
            integration = (
                "BazaarPulse matching and price-position metrics"
                if intent.metric == MetricKey.MARKET_PRICE_GAP
                else "complete freight invoice metrics and reconciled delivered-case denominators"
            )
            return QuestionAnswer(
                status=AnswerStatus.INTEGRATION_REQUIRED,
                summary=f"This question was understood, but {integration} are not yet available.",
                interpretation=intent.interpretation(),
                definition=spec.definition,
                sources=spec.sources,
                warnings=(
                    "No estimate was fabricated. Retry after the governed integration is loaded.",
                ),
                intent=intent,
            )

        try:
            answer = self._dispatch(intent)
        except Exception:
            return QuestionAnswer(
                status=AnswerStatus.ERROR,
                summary="The governed metric service could not complete this question.",
                interpretation=intent.interpretation(),
                definition=spec.definition,
                sources=spec.sources,
                warnings=("No alternate SQL or model-generated calculation was attempted.",),
                intent=intent,
            )
        return answer

    @staticmethod
    def _parse_failure(parsed: ParsedQuestion) -> QuestionAnswer:
        status = (
            AnswerStatus.AMBIGUOUS
            if parsed.status == ParseStatus.AMBIGUOUS
            else AnswerStatus.UNSUPPORTED
        )
        return QuestionAnswer(
            status=status,
            summary=parsed.message,
            interpretation="No metric query was executed.",
            definition="",
            sources=(),
            suggestions=(
                "What was OTIF by customer region for the last complete quarter?",
                "Which five warehouses had the lowest case fill rate last month?",
            ),
        )

    def ask(self, question: str) -> QuestionAnswer:
        """Alias suitable for a UI callback."""

        return self.answer(question)

    def _dispatch(self, intent: QuestionIntent) -> QuestionAnswer:
        dispatchers = {
            MetricKey.FILL_RATE: self._fill_rate,
            MetricKey.STRICT_OTIF: self._otif,
            MetricKey.RETURNS: self._returns,
            MetricKey.CHILLED_EXCURSIONS: self._chilled_excursions,
            MetricKey.LATE_ROUTES: self._late_routes,
            MetricKey.MARKET_PRICE_GAP: self._market_price_gap,
            MetricKey.FREIGHT_PER_CASE: self._freight_per_case,
            MetricKey.DISCONTINUED_SKUS: self._discontinued,
        }
        return dispatchers[intent.metric](intent)

    def _base_answer(
        self,
        intent: QuestionIntent,
        summary: str,
        evidence: tuple[EvidenceBlock, ...],
        *,
        warnings: tuple[str, ...] = (),
    ) -> QuestionAnswer:
        spec = _SPECS[intent.metric]
        status = (
            AnswerStatus.NO_DATA
            if not evidence or all(block.is_empty for block in evidence)
            else AnswerStatus.OK
        )
        if status == AnswerStatus.NO_DATA:
            summary = (
                f"No evidence was found for {intent.period.label} and the interpreted filters."
            )
        return QuestionAnswer(
            status=status,
            summary=summary,
            interpretation=intent.interpretation(),
            definition=spec.definition,
            sources=spec.sources,
            evidence=evidence,
            warnings=warnings,
            intent=intent,
        )

    def _fill_rate(self, intent: QuestionIntent) -> QuestionAnswer:
        if intent.explain_change:
            return self._fill_rate_change(intent)
        filters = intent.filters.to_metric_filters(intent.period)
        if not intent.dimensions:
            metric = self.metric_service.executive_summary(filters, intent.quantity_basis)[
                "fill_rate"
            ]
            evidence = EvidenceBlock(
                "Fill rate",
                "fct_order_service",
                ("value", "delivered_quantity", "ordered_quantity", "orders"),
                ((metric.value, metric.numerator, metric.denominator, metric.records),),
            )
            return self._base_answer(
                intent,
                f"Fill rate was {_format_percent(metric.value)}.",
                (evidence,),
            )

        dimension = intent.dimensions[0]
        if dimension == DimensionKey.MONTH:
            frame = self.metric_service.service_trend(filters, intent.quantity_basis)
            if intent.ranking == Ranking.WORST:
                frame = frame.sort_values("fill_rate", ascending=True)
        else:
            frame = self.metric_service.service_by_dimension(
                filters,
                dimension.value,
                intent.quantity_basis,
                limit=None,
                worst_first=intent.ranking != Ranking.BEST,
            )
            if intent.ranking == Ranking.BEST:
                frame = frame.sort_values("fill_rate", ascending=False)
        if intent.limit is not None:
            frame = frame.head(intent.limit)
        evidence = _frame_evidence("Fill rate breakdown", "fct_order_service", frame)
        if frame.empty:
            summary = "No fill-rate groups were found."
        else:
            first = frame.iloc[0]
            group_column = "dimension_value" if "dimension_value" in frame else "month"
            summary = (
                f"{first[group_column]} is first in the interpreted ranking at "
                f"{_format_percent(first['fill_rate'])} fill rate."
            )
        return self._base_answer(intent, summary, (evidence,))

    def _fill_rate_change(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        duration = (intent.period.end - intent.period.start).days + 1
        prior_end = intent.period.start - timedelta(days=1)
        prior_period = Period(
            prior_end - timedelta(days=duration - 1),
            prior_end,
            "Immediately preceding comparable period",
        )
        previous_filters = replace(
            filters,
            start_date=prior_period.start,
            end_date=prior_period.end,
        )
        current = self.metric_service.executive_summary(filters, intent.quantity_basis)[
            "fill_rate"
        ]
        previous = self.metric_service.executive_summary(
            previous_filters, intent.quantity_basis
        )["fill_rate"]

        rate_evidence = EvidenceBlock(
            "Fill-rate comparison",
            "fct_order_service",
            ("period", "fill_rate", "delivered_quantity", "ordered_quantity", "orders"),
            (
                (
                    intent.period.label,
                    current.value,
                    current.numerator,
                    current.denominator,
                    current.records,
                ),
                (
                    prior_period.label,
                    previous.value,
                    previous.numerator,
                    previous.denominator,
                    previous.records,
                ),
            ),
        )

        short_column = (
            "short_eaches"
            if intent.quantity_basis == QuantityBasis.EACHES
            else "short_case_equivalents"
        )
        contribution_blocks: list[EvidenceBlock] = []
        leading: tuple[str, str, float] | None = None
        for dimension, title in (
            ("short_reason", "Recorded short-reason contribution"),
            ("category", "Category shortage contribution"),
            ("warehouse", "Warehouse shortage contribution"),
        ):
            current_frame = self.metric_service.shortage_contributors(
                filters, dimension, limit=100
            )
            previous_frame = self.metric_service.shortage_contributors(
                previous_filters, dimension, limit=100
            )
            current_projection = current_frame[["dimension_value", short_column]].rename(
                columns={short_column: "current_short_quantity"}
            )
            previous_projection = previous_frame[["dimension_value", short_column]].rename(
                columns={short_column: "previous_short_quantity"}
            )
            comparison = current_projection.merge(
                previous_projection,
                on="dimension_value",
                how="outer",
            ).fillna(0)
            comparison["change_in_short_quantity"] = (
                comparison["current_short_quantity"]
                - comparison["previous_short_quantity"]
            )
            comparison = comparison.sort_values(
                ["change_in_short_quantity", "current_short_quantity"],
                ascending=[False, False],
            ).head(intent.limit or 5)
            contribution_blocks.append(
                _frame_evidence(title, "fct_order_line", comparison)
            )
            if not comparison.empty:
                row = comparison.iloc[0]
                candidate = (
                    dimension,
                    str(row["dimension_value"]),
                    float(row["change_in_short_quantity"]),
                )
                if leading is None or candidate[2] > leading[2]:
                    leading = candidate

        if current.value is None or previous.value is None:
            summary = "A comparable fill-rate change could not be calculated for this scope."
        else:
            change_points = (current.value - previous.value) * 100
            if change_points < -0.05:
                movement = f"fell {abs(change_points):.1f} percentage points"
            elif change_points > 0.05:
                movement = f"rose {change_points:.1f} percentage points"
            else:
                movement = "was unchanged"
            summary = (
                f"Fill rate {movement}, from {previous.value:.1%} to {current.value:.1%}."
            )
            if leading is not None:
                direction = "increase" if leading[2] >= 0 else "decrease"
                summary += (
                    f" The largest measured shortage {direction} across the reviewed "
                    f"dimensions was {leading[0].replace('_', ' ')} {leading[1]} "
                    f"({leading[2]:+,.1f} {intent.quantity_basis.value})."
                )

        return self._base_answer(
            intent,
            summary,
            (rate_evidence, *contribution_blocks),
            warnings=(
                "These are measured period-over-period shortage contributions and associations; "
                "they do not establish causality or blame.",
            ),
        )

    def _otif(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        warnings = (
            "Strict OTIF is expected to be 0% in the supplied extract because every "
            "order line is short-delivered; no tolerance has been substituted.",
        )
        if not intent.dimensions:
            metric = self.metric_service.executive_summary(filters, intent.quantity_basis)[
                "strict_otif"
            ]
            evidence = EvidenceBlock(
                "Strict OTIF",
                "fct_order_service",
                ("value", "otif_orders", "eligible_orders"),
                ((metric.value, metric.numerator, metric.denominator),),
            )
            return self._base_answer(
                intent,
                f"Strict OTIF was {_format_percent(metric.value)}.",
                (evidence,),
                warnings=warnings,
            )

        dimension = intent.dimensions[0]
        if dimension == DimensionKey.MONTH:
            frame = self.metric_service.service_trend(filters, intent.quantity_basis)
        else:
            frame = self.metric_service.service_by_dimension(
                filters, dimension.value, intent.quantity_basis, limit=None
            )
        frame = frame.sort_values("strict_otif_rate", ascending=True)
        if intent.limit is not None:
            frame = frame.head(intent.limit)
        evidence = _frame_evidence("Strict OTIF breakdown", "fct_order_service", frame)
        summary = (
            "No OTIF groups were found."
            if frame.empty
            else f"Strict OTIF is {_format_percent(frame.iloc[0]['strict_otif_rate'])} "
            f"for {frame.iloc[0].get('dimension_value', frame.iloc[0].get('month'))}."
        )
        return self._base_answer(intent, summary, (evidence,), warnings=warnings)

    def _returns(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        evidence: list[EvidenceBlock] = []
        for dimension in intent.dimensions or (DimensionKey.CATEGORY,):
            frame = self.metric_service.returns_by_dimension(
                filters,
                dimension.value,
                statuses=("APPROVED",),
                limit=intent.limit or 20,
            )
            evidence.append(
                _frame_evidence(
                    f"Approved returns by {dimension.value}",
                    "fct_return_credit_note",
                    frame,
                )
            )
        first_nonempty = next((block for block in evidence if block.rows), None)
        if first_nonempty is None:
            summary = "No approved return evidence was found."
        else:
            row = first_nonempty.rows[0]
            amount_index = first_nonempty.columns.index("credit_note_value_inr")
            summary = (
                f"Leading {first_nonempty.title.removeprefix('Approved returns by ')} is "
                f"{row[0]} with ₹{_format_number(row[amount_index])} in approved credit notes."
            )
        return self._base_answer(intent, summary, tuple(evidence))

    def _chilled_excursions(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        if DimensionKey.MONTH in intent.dimensions:
            rows: list[tuple[object, ...]] = []
            for month in _monthly_periods(intent.period):
                month_filters = replace(filters, start_date=month.start, end_date=month.end)
                metric = self.metric_service.executive_summary(month_filters)[
                    "temperature_excursions_per_100"
                ]
                rows.append(
                    (
                        month.label,
                        metric.value,
                        metric.numerator,
                        metric.denominator,
                    )
                )
            evidence = EvidenceBlock(
                "Chilled excursions by month",
                "fct_delivery",
                ("month", "excursions_per_100", "excursions", "chilled_deliveries"),
                tuple(rows),
            )
            available = [row for row in rows if row[1] is not None]
            if available:
                highest = max(available, key=lambda row: _as_float(row[1]))
                summary = (
                    f"The highest monthly chilled-excursion rate was "
                    f"{_format_number(highest[1])} per 100 in {highest[0]}."
                )
            else:
                summary = "No monthly chilled-delivery denominator was available."
            return self._base_answer(intent, summary, (evidence,))

        if intent.dimensions:
            dimension = intent.dimensions[0]
            frame = self.metric_service.cold_chain_by_dimension(
                filters, dimension.value, limit=intent.limit or 20
            )
            evidence = _frame_evidence("Chilled excursion breakdown", "fct_delivery", frame)
            summary = (
                "No chilled-delivery groups were found."
                if frame.empty
                else f"{frame.iloc[0]['dimension_value']} has the highest rate at "
                f"{_format_number(frame.iloc[0]['excursions_per_100'])} per 100."
            )
            return self._base_answer(intent, summary, (evidence,))

        metric = self.metric_service.executive_summary(filters)["temperature_excursions_per_100"]
        evidence = EvidenceBlock(
            "Chilled excursions",
            "fct_delivery",
            ("excursions_per_100", "excursions", "chilled_deliveries"),
            ((metric.value, metric.numerator, metric.denominator),),
        )
        return self._base_answer(
            intent,
            f"Chilled excursions were {_format_number(metric.value)} per 100 deliveries.",
            (evidence,),
        )

    def _late_routes(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        frame = self.metric_service.service_by_dimension(
            filters, "route", QuantityBasis.EACHES, limit=None
        )
        qualifying = frame.loc[frame["late_over_2h_rate"] > intent.rate_threshold].sort_values(
            "late_over_2h_rate", ascending=False
        )
        if intent.limit is not None:
            qualifying = qualifying.head(intent.limit)
        evidence = _frame_evidence(
            "Routes above the late-delivery threshold",
            "fct_order_service",
            qualifying,
        )
        if qualifying.empty:
            summary = "No route exceeded the interpreted late-delivery threshold."
        else:
            first = qualifying.iloc[0]
            summary = (
                f"{len(qualifying)} route(s) exceeded the threshold; "
                f"{first['dimension_value']} was highest at "
                f"{_format_percent(first['late_over_2h_rate'])}."
            )
        return self._base_answer(intent, summary, (evidence,))

    def _discontinued(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        frame = self.metric_service.discontinued_order_evidence(filters, limit=intent.limit or 100)
        evidence = _frame_evidence(
            "Orders placed after SKU discontinuation", "fct_order_line", frame
        )
        summary = f"Showing {len(frame)} order-line record(s) after SKU discontinuation."
        return self._base_answer(intent, summary, (evidence,))

    def _market_price_gap(self, intent: QuestionIntent) -> QuestionAnswer:
        if self.external_service is None:
            raise RuntimeError("External metrics were not supplied")
        filters = intent.filters.to_metric_filters(intent.period)
        city = intent.filters.cities[0] if intent.filters.cities else "Mumbai"
        category = intent.filters.categories[0] if intent.filters.categories else None
        frame = self.external_service.competitor_price_gap(
            filters,
            city=city,
            category=category,
            top_n=intent.limit or 20,
        )
        unavailable_reason = frame.attrs.get("unavailable_reason")
        if frame.empty and unavailable_reason:
            spec = _SPECS[intent.metric]
            return QuestionAnswer(
                status=AnswerStatus.INTEGRATION_REQUIRED,
                summary=str(unavailable_reason),
                interpretation=intent.interpretation(),
                definition=spec.definition,
                sources=spec.sources,
                intent=intent,
            )
        evidence = _frame_evidence(
            f"Latest observed market price gaps in {city}",
            "BazaarPulse observations",
            frame,
        )
        matched = (
            int(frame["lowest_competitor_price_inr"].notna().sum())
            if "lowest_competitor_price_inr" in frame
            else 0
        )
        summary = (
            f"Found defensible competitor prices for {matched} of {len(frame)} top SKU(s) "
            f"in {city}."
        )
        return self._base_answer(
            intent,
            summary,
            (evidence,),
            warnings=("Competitor prices are latest observations, not historical or live prices.",),
        )

    def _freight_per_case(self, intent: QuestionIntent) -> QuestionAnswer:
        if self.external_service is None:
            raise RuntimeError("External metrics were not supplied")
        filters = intent.filters.to_metric_filters(intent.period)
        frame = self.external_service.freight_by_warehouse(filters)
        unavailable_reason = frame.attrs.get("unavailable_reason")
        if frame.empty and unavailable_reason:
            spec = _SPECS[intent.metric]
            return QuestionAnswer(
                status=AnswerStatus.INTEGRATION_REQUIRED,
                summary=str(unavailable_reason),
                interpretation=intent.interpretation(),
                definition=spec.definition,
                sources=spec.sources,
                intent=intent,
            )
        if "freight_cost_per_delivered_case_inr" in frame:
            frame = frame.sort_values(
                "freight_cost_per_delivered_case_inr", ascending=False, na_position="last"
            )
        if intent.limit is not None:
            frame = frame.head(intent.limit)
        evidence = _frame_evidence(
            "Billed freight per delivered case by warehouse",
            "partner freight invoices",
            frame,
        )
        if frame.empty:
            summary = "No freight and delivered-case evidence was found."
        else:
            first = frame.iloc[0]
            summary = (
                f"{first['warehouse_code']} has the highest billed freight per delivered "
                f"case at ₹{_format_number(first['freight_cost_per_delivered_case_inr'])}."
            )
        attribution = frame.attrs.get("attribution")
        warnings = (str(attribution),) if attribution else ()
        return self._base_answer(intent, summary, (evidence,), warnings=warnings)


NLQRouter = QuestionRouter


__all__ = [
    "AnswerStatus",
    "DimensionKey",
    "EvidenceBlock",
    "ExternalMetricService",
    "IntentFilters",
    "MetricKey",
    "MetricService",
    "NLQRouter",
    "ParseStatus",
    "ParsedQuestion",
    "QuestionAnswer",
    "QuestionIntent",
    "QuestionRouter",
    "Ranking",
]
