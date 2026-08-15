"""Governed natural-language routing over the deterministic metric service.

This module deliberately contains no SQL and accepts no SQL-shaped output from a model.
Questions are reduced to an allow-listed, typed intent and dispatched only to public
``AnalyticsService`` methods. The parser is deterministic so the same question and data
anchor always produce the same interpretation.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast

import pandas as pd
from rapidfuzz import fuzz, process

from kestrel.metrics.periods import (
    Period,
    last_complete_month,
    latest_complete_fiscal_quarter,
)
from kestrel.metrics.service import (
    RANKED_COLD_CHAIN_MIN_DELIVERIES,
    FilterSet,
    MetricValue,
    QuantityBasis,
)
from kestrel.nlq.semantic import SemanticIntentResolver, SemanticStatus


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
    ALLOCATION_RATE = "allocation_rate"
    POST_ALLOCATION_FULFILMENT = "post_allocation_fulfilment"
    ON_TIME_RATE = "on_time_rate"
    DELIVERY_ON_TIME_RATE = "delivery_on_time_rate"
    POD_COVERAGE = "pod_coverage"
    DELIVERY_FAILURES = "delivery_failures"
    BACKLOG = "backlog"
    SHORT_DELIVERY_EXPOSURE = "short_delivery_exposure"
    INVENTORY_RISK = "inventory_risk"
    CREDIT_NOTE_LEAKAGE = "credit_note_leakage"
    COMPETITOR_COVERAGE = "competitor_coverage"
    WEATHER_ASSOCIATION = "weather_association"
    HOLIDAY_ASSOCIATION = "holiday_association"


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
    PROMOTION = "promotion"
    PROMOTION_MECHANIC = "promotion_mechanic"
    ORDER_SOURCE = "order_source"
    SHORT_REASON = "short_reason"
    DISPOSITION = "disposition"
    CREDIT_STATUS = "status"
    FAILURE_REASON = "failure_reason"
    TELEMATICS_VENDOR = "telematics_vendor"
    CARRIER = "carrier"


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
    promotion_codes: tuple[str, ...] = ()
    order_sources: tuple[str, ...] = ()
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
            promotion_codes=self.promotion_codes,
            order_sources=self.order_sources,
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
            ("promotion", self.promotion_codes),
            ("order source", self.order_sources),
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
    resolver: str = "rules"
    resolver_provenance: str | None = None
    resolver_confidence: float | None = None
    matched_example: str | None = None
    inherited_fields: tuple[str, ...] = ()
    spelling_corrections: tuple[str, ...] = ()

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
        if self.metric in {
            MetricKey.FILL_RATE,
            MetricKey.STRICT_OTIF,
            MetricKey.ALLOCATION_RATE,
            MetricKey.POST_ALLOCATION_FULFILMENT,
            MetricKey.BACKLOG,
            MetricKey.SHORT_DELIVERY_EXPOSURE,
        }:
            details.append(f"quantity basis={self.quantity_basis.value}")
        if self.limit is not None:
            details.append(f"limit={self.limit}")
        if self.ranking != Ranking.NONE:
            details.append(f"ranking={self.ranking.value}")
        if self.metric == MetricKey.LATE_ROUTES:
            details.append(
                f"threshold=delay > {self.late_threshold_minutes} minutes on "
                f"> {self.rate_threshold:.0%} of deliveries"
            )
        if self.explain_change:
            details.append("analysis=period-over-period measured contribution")
        details.append(f"resolver={self.resolver}")
        if self.resolver_provenance is not None:
            details.append(f"resolver provenance={self.resolver_provenance}")
        if self.resolver_confidence is not None:
            details.append(f"semantic similarity={self.resolver_confidence:.3f}")
        if self.inherited_fields:
            details.append(f"inherited={', '.join(self.inherited_fields)}")
        if self.spelling_corrections:
            details.append(f"spelling={', '.join(self.spelling_corrections)}")
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

    def available_date_range(
        self, date_basis: str = "requested_delivery"
    ) -> tuple[date, date]: ...

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

    def backlog_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int = 30,
    ) -> pd.DataFrame: ...

    def delivery_exception_summary(self, filters: FilterSet) -> dict[str, MetricValue]: ...

    def delivery_exception_trend(self, filters: FilterSet) -> pd.DataFrame: ...

    def delivery_exceptions_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        min_deliveries: int = 50,
        limit: int = 30,
    ) -> pd.DataFrame: ...

    def failure_reason_pareto(self, filters: FilterSet, *, limit: int = 20) -> pd.DataFrame: ...

    def cold_chain_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        min_chilled_deliveries: int = 1,
        limit: int = 20,
    ) -> pd.DataFrame: ...

    def returns_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        statuses: tuple[str, ...] = ("APPROVED",),
        limit: int = 20,
    ) -> pd.DataFrame: ...

    def credit_status_summary(self, filters: FilterSet) -> pd.DataFrame: ...

    def discontinued_order_evidence(
        self, filters: FilterSet, *, limit: int = 100
    ) -> pd.DataFrame: ...

    def shortage_contributors(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int = 20,
    ) -> pd.DataFrame: ...

    def short_delivery_exposure(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int = 30,
    ) -> pd.DataFrame: ...

    def inventory_risk(
        self, filters: FilterSet, dimension: str = "warehouse"
    ) -> pd.DataFrame: ...

    def return_disposition_summary(
        self,
        filters: FilterSet,
        *,
        statuses: tuple[str, ...] = ("APPROVED", "PENDING", "REJECTED"),
    ) -> pd.DataFrame: ...


class ExternalMetricService(Protocol):
    """Optional, fixed-query metrics over governed external snapshots."""

    def freight_by_warehouse(self, filters: FilterSet) -> pd.DataFrame: ...

    def freight_by_route(self, filters: FilterSet) -> pd.DataFrame: ...

    def competitor_price_gap(
        self,
        filters: FilterSet,
        *,
        city: str = "Mumbai",
        category: str | None = None,
        top_n: int = 20,
    ) -> pd.DataFrame: ...

    def competitor_match_quality(self) -> pd.DataFrame: ...


class ContextMetricService(Protocol):
    """Optional, publication-gated weather and holiday associations."""

    def weather_delivery_association(self, filters: FilterSet) -> Any: ...

    def holiday_service_association(self, filters: FilterSet) -> Any: ...


@dataclass(frozen=True)
class _MetricSpec:
    definition: str
    sources: tuple[str, ...]


_SPECS: dict[MetricKey, _MetricSpec] = {
    MetricKey.FILL_RATE: _MetricSpec(
        "Ratio of sums: each eligible order line contributes the lower of delivered and ordered "
        "normalized quantity, divided by ordered normalized quantity, so oversupply on one line "
        "cannot offset a shortfall on another.",
        ("fct_order_service", "orders", "order_lines"),
    ),
    MetricKey.STRICT_OTIF: _MetricSpec(
        "Eligible orders where parsed actual arrival is on or before planned arrival and "
        "every line has delivered eaches >= ordered eaches, divided by eligible orders.",
        ("fct_order_service", "deliveries", "order_lines"),
    ),
    MetricKey.RETURNS: _MetricSpec(
        "Approved credit-note value and normalized absolute return quantity, grouped by "
        "the requested dimension. Pending and rejected notes are excluded from that headline "
        "and shown separately as workflow-status evidence.",
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
        "PAID carrier freight in INR divided by delivered case-equivalents for the same "
        "service period and independently reconciled dimension; other invoice statuses remain "
        "secondary evidence.",
        ("partner freight invoices", "fct_order_line", "warehouses", "routes"),
    ),
    MetricKey.DISCONTINUED_SKUS: _MetricSpec(
        "Order lines whose order date is later than the SKU discontinued date.",
        ("fct_order_line", "orders", "products", "outlets"),
    ),
    MetricKey.ALLOCATION_RATE: _MetricSpec(
        "Allocated normalized quantity divided by ordered normalized quantity for eligible "
        "completed order lines on the requested-delivery cohort.",
        ("fct_order_service", "orders", "order_lines"),
    ),
    MetricKey.POST_ALLOCATION_FULFILMENT: _MetricSpec(
        "Delivered normalized quantity divided by allocated normalized quantity for eligible "
        "completed order lines on the requested-delivery cohort.",
        ("fct_order_service", "orders", "order_lines"),
    ),
    MetricKey.ON_TIME_RATE: _MetricSpec(
        "Requested-delivery-cohort deliveries whose parsed actual arrival is on or before "
        "parsed planned arrival, divided by timestamp-eligible deliveries.",
        ("fct_order_service", "fct_delivery", "orders"),
    ),
    MetricKey.DELIVERY_ON_TIME_RATE: _MetricSpec(
        "Actual-delivery-cohort deliveries whose parsed actual arrival is on or before parsed "
        "planned arrival, divided by deliveries with both timestamps.",
        ("fct_delivery", "deliveries", "orders"),
    ),
    MetricKey.POD_COVERAGE: _MetricSpec(
        "Eligible actual-date deliveries with proof-of-delivery captured divided by all "
        "eligible deliveries.",
        ("fct_delivery", "deliveries"),
    ),
    MetricKey.DELIVERY_FAILURES: _MetricSpec(
        "Recorded delivery-failure labels and their observed frequency on the actual-delivery "
        "cohort; labels are descriptive and do not establish cause or responsibility.",
        ("fct_delivery", "deliveries"),
    ),
    MetricKey.BACKLOG: _MetricSpec(
        "Current OPEN orders whose requested delivery date is on or before the selected as-of "
        "date, restricted to active non-test outlets.",
        ("fct_order_service", "orders", "outlets"),
    ),
    MetricKey.SHORT_DELIVERY_EXPOSURE: _MetricSpec(
        "Booked line value multiplied by the positive undelivered share at order-line grain; "
        "this is commercial exposure, not accounting loss or profit.",
        ("fct_order_line", "orders", "order_lines"),
    ),
    MetricKey.INVENTORY_RISK: _MetricSpec(
        "Available, near-expiry, expired, damaged, and blocked cases from the latest weekly "
        "inventory snapshot on or before the selected period end.",
        ("fct_inventory_snapshot", "inventory_snapshots", "products", "warehouses"),
    ),
    MetricKey.CREDIT_NOTE_LEAKAGE: _MetricSpec(
        "Approved credit-note value divided by estimated delivered booked value for the "
        "period; workflow values and dispositions remain separate evidence.",
        ("fct_return_credit_note", "fct_order_line", "returns_credit_notes"),
    ),
    MetricKey.COMPETITOR_COVERAGE: _MetricSpec(
        "Current BazaarPulse listings with a governed final matched outcome divided by all "
        "current collected listings.",
        ("ext_bazaarpulse_listing_current", "ext_bazaarpulse_match_current"),
    ),
    MetricKey.WEATHER_ASSOCIATION: _MetricSpec(
        "Observed service metrics on rainy warehouse-city days versus little/no-rain days, "
        "published only after completeness, join-coverage, and cohort gates pass.",
        ("ext_weather_daily_current", "fct_order_service"),
    ),
    MetricKey.HOLIDAY_ASSOCIATION: _MetricSpec(
        "Observed service metrics on national public holidays versus other requested-delivery "
        "days, published only after completeness and cohort gates pass.",
        ("ext_india_holiday_current", "fct_order_service"),
    ),
}


_METRIC_PATTERNS: dict[MetricKey, tuple[str, ...]] = {
    MetricKey.FILL_RATE: (r"\bfill[ -]?rate\b",),
    MetricKey.STRICT_OTIF: (
        r"\botif\b",
        r"\bon[ -]?time[ -]?in[ -]?full\b",
        r"\bon[ -]?time\b.*\b(?:complete(?:ly)?|fully)?\s*in[ -]?full\b",
        r"\b(?:complete(?:ly)?|fully)?\s*in[ -]?full\b.*\bon[ -]?time\b",
        r"\bcomplete orders?\b.*\bpromised time\b",
    ),
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
    MetricKey.ALLOCATION_RATE: (
        r"\ballocation[ -]?rate\b",
        r"\b(?:orders?|quantity)\b.*\ballocated\b",
    ),
    MetricKey.POST_ALLOCATION_FULFILMENT: (
        r"\bpost[ -]?allocation\b",
        r"\bdelivered\b.*\ballocated\b",
        r"\ballocated\b.*\bdelivered\b",
    ),
    MetricKey.ON_TIME_RATE: (
        r"\bon[ -]?time\b(?![ -]?in[ -]?full)",
        r"\btimestamp[ -]?derived timing\b",
    ),
    MetricKey.DELIVERY_ON_TIME_RATE: (
        r"\b(?:actual[ -]date deliveries|actual deliveries|actual[ -]delivery[ -]date|"
        r"actual[ -]delivery cohort|delivery[ -]date cohort)\b.*\bon[ -]?time\b",
        r"\bon[ -]?time\b.*\b(?:actual[ -]date deliveries|actual deliveries|"
        r"actual[ -]delivery[ -]date|actual[ -]delivery cohort|delivery[ -]date cohort)\b",
        r"\bactual[ -]delivery[ -](?:date )?(?:timing|punctuality|performance)\b",
    ),
    MetricKey.POD_COVERAGE: (
        r"\bpod\b",
        r"\bproof[ -]?of[ -]?delivery\b",
    ),
    MetricKey.DELIVERY_FAILURES: (
        r"\bdelivery failures?\b",
        r"\bdelivery failure rate\b",
        r"\brecorded (?:delivery )?failure(?: signal)? rate\b",
        r"\bfailure reasons?\b",
        r"\bfailed deliveries\b",
    ),
    MetricKey.BACKLOG: (
        r"\bbacklog\b",
        r"\boverdue open orders?\b",
        r"\bopen orders?\b.*\boverdue\b",
    ),
    MetricKey.SHORT_DELIVERY_EXPOSURE: (
        r"\bshort[ -]?delivery\b.*\b(?:value|exposure|inr)\b",
        r"\bbooked[ -]?value exposure\b",
        r"\bcommercial exposure\b.*\bshort",
    ),
    MetricKey.INVENTORY_RISK: (
        r"\binventory risk\b",
        r"\bnear[ -]?expiry\b",
        r"\bstock close to expiry\b",
        r"\b(?:cases?|stock) (?:approaching|close to) expiry\b",
    ),
    MetricKey.CREDIT_NOTE_LEAKAGE: (
        r"\bcredit[ -]?note\b.*\b(?:rate|leakage|exposure)\b",
        r"\bapproved credit\b.*\b(?:rate|leakage)\b",
        r"\bcommercial leakage\b.*\bcredit",
        r"\bapproved returns? leakage(?: rate)?\b",
        r"\bcredit leakage\b.*\bdelivered value\b",
        r"\bcredit[ -]?note value\b.*\brelative to deliveries\b",
        r"\bdelivered value\b.*\b(?:approved )?credit[ -]?notes?\b",
    ),
    MetricKey.COMPETITOR_COVERAGE: (
        r"\bcompetitor[ -]?match coverage\b",
        r"\bcompetitor coverage\b",
        r"\bmarket[ -]?match coverage\b",
        r"\bmarket[ -]?price matching coverage\b",
        r"\breliable competitor match\b",
        r"\bcompetitor (?:product )?matches? missing\b",
        r"\bcomplete\b.*\b(?:bazaarpulse|competitor|market)[ -]?matching evidence\b",
        r"\b(?:bazaarpulse|competitor|market)[ -]?matching evidence\b.*\bcomplete\b",
    ),
    MetricKey.WEATHER_ASSOCIATION: (
        r"\bweather\b",
        r"\brain(?:y|fall)?\b.*\b(?:service|delivery|fill|late)",
    ),
    MetricKey.HOLIDAY_ASSOCIATION: (
        r"\bholidays?\b.*\b(?:service|delivery|fill|late)",
        r"\b(?:service|delivery|fill|late)\b.*\bholidays?\b",
    ),
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
            DimensionKey.PROMOTION,
            DimensionKey.PROMOTION_MECHANIC,
            DimensionKey.ORDER_SOURCE,
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
            DimensionKey.PROMOTION,
            DimensionKey.PROMOTION_MECHANIC,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.RETURNS: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.CATEGORY,
            DimensionKey.SKU,
            DimensionKey.RETURN_REASON,
            DimensionKey.DISPOSITION,
            DimensionKey.CREDIT_STATUS,
            DimensionKey.PROMOTION,
            DimensionKey.PROMOTION_MECHANIC,
            DimensionKey.ORDER_SOURCE,
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
            DimensionKey.CATEGORY,
            DimensionKey.PROMOTION,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.LATE_ROUTES: frozenset({DimensionKey.ROUTE}),
    MetricKey.MARKET_PRICE_GAP: frozenset({DimensionKey.SKU}),
    MetricKey.FREIGHT_PER_CASE: frozenset(
        {DimensionKey.WAREHOUSE, DimensionKey.ROUTE}
    ),
    MetricKey.DISCONTINUED_SKUS: frozenset(
        {DimensionKey.OUTLET, DimensionKey.SKU, DimensionKey.WAREHOUSE}
    ),
    MetricKey.ALLOCATION_RATE: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.MONTH,
            DimensionKey.PROMOTION,
            DimensionKey.PROMOTION_MECHANIC,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.POST_ALLOCATION_FULFILMENT: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.MONTH,
            DimensionKey.PROMOTION,
            DimensionKey.PROMOTION_MECHANIC,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.ON_TIME_RATE: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.MONTH,
            DimensionKey.PROMOTION,
            DimensionKey.PROMOTION_MECHANIC,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.DELIVERY_ON_TIME_RATE: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.MONTH,
            DimensionKey.TELEMATICS_VENDOR,
            DimensionKey.PROMOTION,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.POD_COVERAGE: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.MONTH,
            DimensionKey.TELEMATICS_VENDOR,
            DimensionKey.PROMOTION,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.DELIVERY_FAILURES: frozenset(
        {
            DimensionKey.FAILURE_REASON,
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.MONTH,
            DimensionKey.TELEMATICS_VENDOR,
            DimensionKey.PROMOTION,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.BACKLOG: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.PROMOTION,
            DimensionKey.PROMOTION_MECHANIC,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.SHORT_DELIVERY_EXPOSURE: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.CATEGORY,
            DimensionKey.SKU,
            DimensionKey.SHORT_REASON,
            DimensionKey.PROMOTION,
            DimensionKey.PROMOTION_MECHANIC,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.INVENTORY_RISK: frozenset(
        {
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.CATEGORY,
            DimensionKey.SKU,
        }
    ),
    MetricKey.CREDIT_NOTE_LEAKAGE: frozenset(
        {
            DimensionKey.CUSTOMER_REGION,
            DimensionKey.WAREHOUSE_REGION,
            DimensionKey.WAREHOUSE,
            DimensionKey.ROUTE,
            DimensionKey.OUTLET,
            DimensionKey.CHANNEL,
            DimensionKey.CATEGORY,
            DimensionKey.SKU,
            DimensionKey.RETURN_REASON,
            DimensionKey.DISPOSITION,
            DimensionKey.CREDIT_STATUS,
            DimensionKey.PROMOTION,
            DimensionKey.PROMOTION_MECHANIC,
            DimensionKey.ORDER_SOURCE,
        }
    ),
    MetricKey.COMPETITOR_COVERAGE: frozenset(),
    MetricKey.WEATHER_ASSOCIATION: frozenset(),
    MetricKey.HOLIDAY_ASSOCIATION: frozenset(),
}

_OPERATIONAL_FILTER_FIELDS = frozenset(
    {
        "customer_regions",
        "warehouse_regions",
        "warehouse_codes",
        "route_codes",
        "outlet_codes",
        "channels",
        "promotion_codes",
        "order_sources",
    }
)
_ALL_INTENT_FILTER_FIELDS = frozenset(
    {field.name for field in fields(IntentFilters)}
)
_DATE_BASIS_BY_METRIC: dict[MetricKey, str] = {
    MetricKey.CHILLED_EXCURSIONS: "actual_delivery",
    MetricKey.LATE_ROUTES: "actual_delivery",
    MetricKey.DELIVERY_ON_TIME_RATE: "actual_delivery",
    MetricKey.POD_COVERAGE: "actual_delivery",
    MetricKey.DELIVERY_FAILURES: "actual_delivery",
    MetricKey.WEATHER_ASSOCIATION: "actual_delivery",
    MetricKey.RETURNS: "returns",
    MetricKey.CREDIT_NOTE_LEAKAGE: "returns",
    MetricKey.INVENTORY_RISK: "inventory",
}
_ADVERSE_METRICS = frozenset(
    {
        MetricKey.RETURNS,
        MetricKey.CHILLED_EXCURSIONS,
        MetricKey.LATE_ROUTES,
        MetricKey.MARKET_PRICE_GAP,
        MetricKey.FREIGHT_PER_CASE,
        MetricKey.DELIVERY_FAILURES,
        MetricKey.BACKLOG,
        MetricKey.SHORT_DELIVERY_EXPOSURE,
        MetricKey.INVENTORY_RISK,
        MetricKey.CREDIT_NOTE_LEAKAGE,
    }
)


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
_FORECAST_REQUEST = re.compile(
    r"\b(?:forecast|predict(?:ion|ive|ed)?|project(?:ion|ed)?|future estimate|"
    r"expected performance)\b"
    r"|\b(?:next|upcoming)\s+(?:day|week|month|quarter|year|fiscal quarter)\b"
    r"|\b(?:tomorrow|what will|will\s+\w+(?:\s+\w+){0,5}\s+be)\b"
)
_UNSUPPORTED_FINANCIAL_REQUEST = re.compile(
    r"\b(?:accounting profit|net profit|profit|net margin|revenue|cash recovery|"
    r"cash leakage|financial losses?|money (?:lost|made)|sales(?!\s+regions?\b))\b"
)
_PRESCRIPTIVE_REQUEST = re.compile(
    r"\b(?:should (?:we|i|kestrel)|recommend(?:ation|ed)?|optimal|optimise|optimize|"
    r"how much (?:inventory|stock|product) (?:should|to) (?:order|buy)|"
    r"reorder (?:point|quantity)|safety stock)\b"
)
_CAUSAL_ATTRIBUTION_REQUEST = re.compile(
    r"\b(?:who (?:is|was) responsible|who caused|assign blame|root cause|"
    r"responsible (?:team|person|party))\b"
)
_UNSUPPORTED_TOPIC_REQUEST = re.compile(
    r"\b(?:customer satisfaction|sentiment|headcount|market share|production plan|"
    r"supplier performance|procurement|(?:damaged|blocked|expired) "
    r"(?:inventory|stock|cases?))\b"
)

_SPELLING_ALIASES = {
    "alloction": "allocation",
    "allocaton": "allocation",
    "alllocation": "allocation",
    "competetor": "competitor",
    "delievery": "delivery",
    "delivary": "delivery",
    "discontinud": "discontinued",
    "expirty": "expiry",
    "fil": "fill",
    "forcast": "forecast",
    "forecsat": "forecast",
    "frieght": "freight",
    "fullfilment": "fulfilment",
    "fulfiment": "fulfilment",
    "inventry": "inventory",
    "lossses": "losses",
    "outelts": "outlets",
    "outltes": "outlets",
    "predcit": "predict",
    "profitt": "profit",
    "revanue": "revenue",
    "retuns": "returns",
    "rte": "rate",
    "temparature": "temperature",
    "warehose": "warehouse",
    "warehoses": "warehouses",
    "wheather": "weather",
}


def normalize_business_spelling(question: str) -> tuple[str, tuple[str, ...]]:
    """Correct only high-confidence business vocabulary, never entity identifiers.

    Only reviewed typo aliases are auto-applied.  Free-form fuzzy matches are never
    rewritten because changing a valid word (for example ``router`` to ``route``) can
    change the requested metric. Corrections remain visible in answer provenance.
    """

    normalized = re.sub(
        r"\s+", " ", question.strip().casefold().replace("–", "-").replace("—", "-")
    )
    corrections: list[str] = []

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        replacement = _SPELLING_ALIASES.get(token)
        if replacement is None or replacement == token:
            return token
        corrections.append(f"{token}→{replacement}")
        return replacement

    corrected = re.sub(r"(?<![\w-])[a-z][a-z-]{2,}(?![\w-])", replace_token, normalized)
    return corrected, tuple(dict.fromkeys(corrections))


def _suggest_option(candidate: str, options: tuple[str, ...] | list[str]) -> str | None:
    if not options:
        return None
    result = process.extractOne(candidate, options, scorer=fuzz.WRatio, score_cutoff=70)
    return str(result[0]) if result is not None else None


def _coerce_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _unsupported_request_message(text: str) -> str | None:
    """Return a fail-closed message for requests outside historical reporting."""

    if _FORECAST_REQUEST.search(text):
        return (
            "Ask Kestrel reports governed historical measures; it does not forecast, "
            "predict, or answer for a future period."
        )
    if _UNSUPPORTED_FINANCIAL_REQUEST.search(text):
        return (
            "The supplied data does not support accounting profit, net margin, revenue, "
            "cash recovery, or total financial-loss answers. Ask for a governed commercial "
            "exposure or service metric instead."
        )
    if _PRESCRIPTIVE_REQUEST.search(text):
        return (
            "Ask Kestrel describes governed historical evidence; it does not prescribe "
            "order quantities, routes, stock policy, or business actions."
        )
    if _CAUSAL_ATTRIBUTION_REQUEST.search(text):
        return (
            "The supplied evidence does not establish root cause, responsibility, or blame. "
            "Ask for a recorded reason, exception rate, or measured contribution instead."
        )
    if _UNSUPPORTED_TOPIC_REQUEST.search(text):
        return (
            "That topic is not represented by a governed Ask Kestrel metric. Rephrase using "
            "one of the supported service, delivery, near-expiry, freight, returns, exposure, "
            "or market measures."
        )
    return None


def _available_date_range(
    service: MetricService, metric: MetricKey
) -> tuple[date, date]:
    """Resolve the governed date cohort while retaining compatibility with small test doubles."""

    basis = _DATE_BASIS_BY_METRIC.get(metric, "requested_delivery")
    try:
        minimum, maximum = service.available_date_range(basis)
    except TypeError:
        # Third-party/test implementations of the narrow protocol may pre-date date-basis
        # support. Their historical no-argument range remains a safe compatibility fallback.
        minimum, maximum = service.available_date_range()
    return _coerce_date(minimum), _coerce_date(maximum)


def _allowed_filter_fields(intent: QuestionIntent) -> frozenset[str]:
    """Return filters whose values are actually consumed by the selected handler."""

    if intent.metric == MetricKey.MARKET_PRICE_GAP:
        return _ALL_INTENT_FILTER_FIELDS
    if intent.metric == MetricKey.COMPETITOR_COVERAGE:
        return frozenset({"cities"})
    if intent.metric == MetricKey.INVENTORY_RISK:
        return frozenset({"warehouse_regions", "warehouse_codes"})
    if intent.metric == MetricKey.FREIGHT_PER_CASE:
        if intent.dimensions == (DimensionKey.ROUTE,):
            return frozenset({"route_codes"})
        return frozenset({"warehouse_regions", "warehouse_codes"})
    return _OPERATIONAL_FILTER_FIELDS


def _unsupported_filter_fields(intent: QuestionIntent) -> tuple[str, ...]:
    allowed = _allowed_filter_fields(intent)
    return tuple(
        field.name
        for field in fields(IntentFilters)
        if field.name not in allowed and getattr(intent.filters, field.name)
    )


def _multi_dimension_error(intent: QuestionIntent) -> str | None:
    if len(intent.dimensions) <= 1:
        return None
    if intent.metric == MetricKey.DISCONTINUED_SKUS and set(intent.dimensions) == {
        DimensionKey.OUTLET,
        DimensionKey.SKU,
    }:
        return None
    names = ", ".join(dimension.value for dimension in intent.dimensions)
    return (
        f"{intent.metric.value} received multiple groupings ({names}). "
        "Ask for one grouping dimension at a time."
    )


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
    matched = set(matches)
    # Specific governed concepts take precedence over their broader vocabulary families.
    if MetricKey.POST_ALLOCATION_FULFILMENT in matched:
        matched.discard(MetricKey.ALLOCATION_RATE)
    if MetricKey.STRICT_OTIF in matched:
        matched.discard(MetricKey.ON_TIME_RATE)
        matched.discard(MetricKey.DELIVERY_ON_TIME_RATE)
    if MetricKey.DELIVERY_ON_TIME_RATE in matched:
        matched.discard(MetricKey.ON_TIME_RATE)
    if MetricKey.CREDIT_NOTE_LEAKAGE in matched:
        matched.discard(MetricKey.RETURNS)
    if MetricKey.COMPETITOR_COVERAGE in matched:
        matched.discard(MetricKey.MARKET_PRICE_GAP)
    matches = [metric for metric in matches if metric in matched]
    return tuple(matches)


def _detect_dimensions(
    text: str, metric: MetricKey | None = None
) -> tuple[DimensionKey, ...]:
    dimensions: list[DimensionKey] = []
    warehouse_region = bool(
        re.search(
            r"\b(?:by|per|across)\s+(?:warehouse|dc|distribution centre) regions?\b"
            r"|\b(?:which|show|give|rank)(?:\s+\w+){0,5}\s+"
            r"(?:warehouse|dc|distribution centre) regions?\b",
            text,
        )
    )
    if warehouse_region:
        dimensions.append(DimensionKey.WAREHOUSE_REGION)
    if re.search(
        r"\b(?:by|per|across)\s+(?:customer |sales )?regions?\b"
        r"|\b(?:which|show|give|rank)(?:\s+\w+){0,5}\s+"
        r"(?:customer|sales) regions?\b",
        text,
    ):
        dimensions.append(DimensionKey.CUSTOMER_REGION)
    if not warehouse_region and re.search(
        r"\b(?:by|per|across|and)\s+"
        r"(?:dc|dcs|depots?|warehouse|warehouses|distribution centres?)\b"
        r"|\b(?:which|show|give|rank)(?:\s+\w+){0,6}\s+"
        r"(?:dc|dcs|depots?|warehouse|warehouses|distribution centres?)\b",
        text,
    ):
        dimensions.append(DimensionKey.WAREHOUSE)
    if re.search(r"\b(?:by|per|across|and)\s+routes?\b", text) or re.search(
        r"\b(?:which|show|give|rank)(?:\s+\w+){0,5}\s+routes?\b", text
    ):
        dimensions.append(DimensionKey.ROUTE)
    if re.search(
        r"\b(?:by|per|across|and)\s+(?:outlets?|customers?|stores?)(?!\s+regions?\b)",
        text,
    ) or re.search(
        r"\b(?:which|show|give|rank)(?:\s+\w+){0,4}\s+"
        r"(?:outlets?|customers?|stores?)\b(?!\s+regions?\b)",
        text,
    ):
        dimensions.append(DimensionKey.OUTLET)
    if re.search(r"\b(?:by|per|across|and)\s+channels?\b", text):
        dimensions.append(DimensionKey.CHANNEL)
    if re.search(r"\b(?:by|per|across|which|and)\s+(?:categories|category)\b", text):
        dimensions.append(DimensionKey.CATEGORY)
    if re.search(r"\b(?:reason|reasons|reason code|reason codes)\b", text):
        if metric == MetricKey.DELIVERY_FAILURES:
            dimensions.append(DimensionKey.FAILURE_REASON)
        elif metric == MetricKey.SHORT_DELIVERY_EXPOSURE:
            dimensions.append(DimensionKey.SHORT_REASON)
        else:
            dimensions.append(DimensionKey.RETURN_REASON)
    if re.search(
        r"\b(?:by|per|across|which)\s+(?:months?|periods?)\b|\bmonthly\b",
        text,
    ):
        dimensions.append(DimensionKey.MONTH)
    if re.search(
        r"\b(?:by|per|and|top\s+\w+)\s+(?:skus?|products?)\b"
        r"|\b(?:which|show|give|rank)(?:\s+\w+){0,5}\s+(?:skus?|products?)\b",
        text,
    ):
        dimensions.append(DimensionKey.SKU)
    if re.search(r"\b(?:by|per|across|and)\s+promotions?\b(?!\s+mechanics?\b)", text):
        dimensions.append(DimensionKey.PROMOTION)
    if re.search(r"\b(?:by|per|across|and)\s+promotion mechanics?\b", text):
        dimensions.append(DimensionKey.PROMOTION_MECHANIC)
    if re.search(r"\b(?:by|per|across|and)\s+(?:order )?sources?\b", text):
        dimensions.append(DimensionKey.ORDER_SOURCE)
    if re.search(r"\b(?:by|per|across)\s+dispositions?\b", text):
        dimensions.append(DimensionKey.DISPOSITION)
    if re.search(r"\b(?:by|per|across)\s+(?:credit(?: note)? )?statuses?\b", text):
        dimensions.append(DimensionKey.CREDIT_STATUS)
    if re.search(r"\b(?:by|per|across)\s+telematics vendors?\b", text):
        dimensions.append(DimensionKey.TELEMATICS_VENDOR)
    if re.search(r"\b(?:by|per|across)\s+carriers?\b", text):
        dimensions.append(DimensionKey.CARRIER)
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
        MetricKey.DELIVERY_FAILURES: (DimensionKey.FAILURE_REASON,),
    }
    return defaults.get(metric, ())


def _extract_limit(text: str, metric: MetricKey) -> int | None:
    match = re.search(
        r"\b(?:top|bottom|lowest|smallest|highest|largest|worst|best|which)\s+"
        r"(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|hundred)\b",
        text,
    )
    if match:
        raw = match.group(1)
        limit = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        return min(max(limit, 1), 100)
    reverse_match = re.search(
        r"\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|twenty|"
        r"fifty|hundred)\s+(?:lowest|smallest|highest|largest|worst|best|bottom|top|poorest)\b",
        text,
    )
    if reverse_match:
        raw = reverse_match.group(1)
        limit = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        return min(max(limit, 1), 100)
    defaults = {
        MetricKey.MARKET_PRICE_GAP: 20,
        MetricKey.DISCONTINUED_SKUS: 100,
    }
    return defaults.get(metric)


def _extract_ranking(text: str, metric: MetricKey | None = None) -> Ranking:
    if metric == MetricKey.MARKET_PRICE_GAP and re.search(
        r"\btop\s+(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"twenty|fifty|hundred)\s+(?:skus?|products?)\s+by\s+value\b",
        text,
    ):
        return Ranking.NONE
    if metric == MetricKey.MARKET_PRICE_GAP:
        text = re.sub(r"\blowest competitor price\b", "", text)
    adverse = metric in _ADVERSE_METRICS
    if re.search(r"\b(?:worst|poorest)\b", text):
        return Ranking.WORST
    if re.search(r"\bbest\b", text):
        return Ranking.BEST
    if re.search(r"\b(?:lowest|smallest|bottom)\b", text):
        return Ranking.BEST if adverse else Ranking.WORST
    if re.search(r"\b(?:highest|largest|top|most)\b", text):
        return Ranking.WORST if adverse else Ranking.BEST
    if re.search(r"\brank(?:ed|ing)?\b", text):
        return Ranking.WORST
    return Ranking.NONE


def _ranking_ascending(intent: QuestionIntent, *, adverse: bool) -> bool:
    """Translate best/worst semantics into an explicit numeric sort direction."""

    if intent.ranking == Ranking.BEST:
        return adverse
    if intent.ranking == Ranking.WORST:
        return not adverse
    # Unqualified ranked views surface the weakest/highest-risk group first.
    return not adverse


def _ranking_label(intent: QuestionIntent, *, adverse: bool) -> str:
    return "lowest" if _ranking_ascending(intent, adverse=adverse) else "highest"


def _extract_quantity_basis(
    text: str, metric: MetricKey
) -> tuple[QuantityBasis | None, str | None]:
    if metric not in {
        MetricKey.FILL_RATE,
        MetricKey.STRICT_OTIF,
        MetricKey.ALLOCATION_RATE,
        MetricKey.POST_ALLOCATION_FULFILMENT,
        MetricKey.BACKLOG,
        MetricKey.SHORT_DELIVERY_EXPOSURE,
    }:
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
    customer_region_context = bool(
        re.search(r"\b(?:customer|sales) regions?\b", text)
    )
    if warehouse_region_context and customer_region_context:
        return (
            IntentFilters(),
            "The question names both customer and DC/warehouse region contexts. "
            "Ask for one regional lens at a time.",
        )
    customer_regions: tuple[str, ...] = ()
    warehouse_regions: tuple[str, ...] = ()
    customer_region_options = options.get("customer_regions", [])
    warehouse_region_options = options.get("warehouse_regions", [])
    matched_customer_regions = _matching_options(text, customer_region_options)
    matched_warehouse_regions = _matching_options(text, warehouse_region_options)
    if warehouse_region_context:
        warehouse_regions = matched_warehouse_regions
    elif customer_region_context:
        customer_regions = matched_customer_regions

    unknown_region = re.search(r"\b(?:in|for)\s+([a-z][a-z -]+?)\s+region\b", text)
    if unknown_region and not (matched_customer_regions or matched_warehouse_regions):
        candidate = unknown_region.group(1).strip()
        if candidate not in {"customer", "sales", "warehouse", "dc"}:
            suggestion = _suggest_option(
                candidate,
                tuple(dict.fromkeys((*customer_region_options, *warehouse_region_options))),
            )
            suffix = f" Did you mean '{suggestion}'?" if suggestion else ""
            return IntentFilters(), f"Unknown region '{candidate.title()}'.{suffix}"

    if not (warehouse_region_context or customer_region_context):
        if matched_customer_regions and matched_warehouse_regions:
            names = ", ".join(
                dict.fromkeys((*matched_customer_regions, *matched_warehouse_regions))
            )
            return (
                IntentFilters(),
                f"Does '{names}' mean customer region or DC/warehouse region? "
                "State the regional lens explicitly; no metric query was run.",
            )
        if re.search(r"\b(?:by|per|across)\s+regions?\b", text):
            return (
                IntentFilters(),
                "Does 'region' mean customer region or DC/warehouse region? "
                "State the regional lens explicitly; no metric query was run.",
            )
        customer_regions = matched_customer_regions
        warehouse_regions = matched_warehouse_regions

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
        suggestion_parts: list[str] = []
        for code in unknown_codes:
            candidates = (
                tuple(valid_warehouses)
                if code.startswith("WH")
                else tuple(valid_routes)
                if code.startswith("RT")
                else tuple(valid_outlets)
            )
            suggestion = _suggest_option(code, candidates)
            if suggestion:
                suggestion_parts.append(f"{code}→{suggestion}")
        suffix = (
            " Did you mean " + ", ".join(suggestion_parts) + "?"
            if suggestion_parts
            else ""
        )
        return (
            IntentFilters(),
            f"Unknown filter code(s): {', '.join(unknown_codes)}.{suffix}",
        )

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
    categories = _matching_options(text, options.get("categories", []))
    promotion_codes = _matching_options(text, options.get("promotion_codes", []))
    order_sources = _matching_options(text, options.get("order_sources", []))
    return (
        IntentFilters(
            customer_regions=customer_regions,
            warehouse_regions=warehouse_regions,
            warehouse_codes=warehouse_codes,
            route_codes=route_codes,
            outlet_codes=outlet_codes,
            channels=tuple(dict.fromkeys(channels)),
            promotion_codes=promotion_codes,
            order_sources=order_sources,
            cities=cities,
            categories=categories,
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
    if value is None:
        return "not available"
    numeric = _as_float(value)
    displayed_percent = numeric * 100
    absolute = abs(displayed_percent)
    if 0 < absolute < 0.001:
        return "<0.001%" if displayed_percent > 0 else ">-0.001%"
    if 0 < absolute < 0.1:
        return f"{displayed_percent:.3f}%"
    return f"{displayed_percent:.1f}%"


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
        context_service: ContextMetricService | None = None,
        semantic_resolver: SemanticIntentResolver | None = None,
    ) -> None:
        self.metric_service = metric_service
        self.external_service = external_service
        self.context_service = context_service
        self.semantic_resolver = semantic_resolver

    def parse(self, question: str) -> ParsedQuestion:
        stripped = question.strip()
        if not stripped:
            return ParsedQuestion(
                ParseStatus.UNSUPPORTED,
                None,
                "Enter a supply-chain question.",
            )
        text, spelling_corrections = normalize_business_spelling(stripped)
        unsupported_message = _unsupported_request_message(text)
        if unsupported_message is not None:
            return ParsedQuestion(
                ParseStatus.UNSUPPORTED,
                None,
                unsupported_message,
            )
        metrics = _detect_metrics(text)
        semantic_resolution = None
        if not metrics:
            if self.semantic_resolver is not None:
                semantic_resolution = self.semantic_resolver.resolve(
                    text,
                    allowed_intents={metric.value for metric in MetricKey},
                )
                if semantic_resolution.status == SemanticStatus.READY:
                    try:
                        metrics = (MetricKey(str(semantic_resolution.intent_key)),)
                    except ValueError:
                        metrics = ()
                elif semantic_resolution.status == SemanticStatus.AMBIGUOUS:
                    return ParsedQuestion(
                        ParseStatus.AMBIGUOUS,
                        None,
                        semantic_resolution.message,
                    )
            if not metrics:
                return ParsedQuestion(
                    ParseStatus.UNSUPPORTED,
                    None,
                    "I can answer governed service, delivery, inventory, leakage, freight, "
                    "market, weather, and holiday questions. Name a metric or rephrase the "
                    "business outcome you want to review.",
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

        minimum, maximum = _available_date_range(self.metric_service, metric)
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

        dimensions = _default_dimensions(metric, _detect_dimensions(text, metric))
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
            ranking=_extract_ranking(text, metric),
            limit=_extract_limit(text, metric),
            explain_change=(
                metric == MetricKey.FILL_RATE
                and bool(
                    re.search(r"\bwhy\b", text)
                    or re.search(r"\b(?:dropped|changed|declined)\b", text)
                )
            ),
            resolver=(
                "local_semantic"
                if semantic_resolution is not None
                else "rules+spelling"
                if spelling_corrections
                else "rules"
            ),
            resolver_provenance=(
                semantic_resolution.provenance.label
                if semantic_resolution is not None
                else None
            ),
            resolver_confidence=(
                semantic_resolution.confidence
                if semantic_resolution is not None
                else None
            ),
            matched_example=(
                semantic_resolution.matched_example
                if semantic_resolution is not None
                else None
            ),
            spelling_corrections=spelling_corrections,
        )
        validated = self.validate_intent(intent)
        if validated.status != ParseStatus.READY:
            return validated
        return ParsedQuestion(ParseStatus.READY, intent, "Question interpreted.")

    def answer(self, question: str) -> QuestionAnswer:
        return self.answer_parsed(self.parse(question))

    def answer_parsed(self, parsed: ParsedQuestion) -> QuestionAnswer:
        """Answer one parse result without repeating semantic inference."""

        if parsed.status != ParseStatus.READY or parsed.intent is None:
            return self._parse_failure(parsed)
        return self.answer_intent(parsed.intent)

    @staticmethod
    def validate_intent(intent: QuestionIntent) -> ParsedQuestion:
        """Validate a typed intent before dispatch, including memory-merged follow-ups."""

        multiple_dimension_error = _multi_dimension_error(intent)
        if multiple_dimension_error is not None:
            return ParsedQuestion(
                ParseStatus.AMBIGUOUS,
                None,
                multiple_dimension_error,
                (intent.metric,),
            )

        unsupported = tuple(
            dimension
            for dimension in intent.dimensions
            if dimension not in _ALLOWED_DIMENSIONS[intent.metric]
        )
        if unsupported:
            names = ", ".join(dimension.value for dimension in unsupported)
            return ParsedQuestion(
                ParseStatus.AMBIGUOUS,
                None,
                f"{intent.metric.value} does not support dimension(s): {names}.",
                (intent.metric,),
            )
        unsupported_filters = _unsupported_filter_fields(intent)
        if unsupported_filters:
            names = ", ".join(name.replace("_", " ") for name in unsupported_filters)
            return ParsedQuestion(
                ParseStatus.AMBIGUOUS,
                None,
                f"{intent.metric.value} cannot apply active filter(s): {names}. "
                "Remove those filters or ask for a metric that supports them; no query was run.",
                (intent.metric,),
            )
        if intent.period.start > intent.period.end:
            return ParsedQuestion(
                ParseStatus.AMBIGUOUS,
                None,
                "The interpreted start date is after the end date.",
                (intent.metric,),
            )
        if intent.explain_change and intent.metric != MetricKey.FILL_RATE:
            return ParsedQuestion(
                ParseStatus.AMBIGUOUS,
                None,
                "A measured 'why did it change' follow-up is currently supported only for "
                "fill rate.",
                (intent.metric,),
            )
        return ParsedQuestion(ParseStatus.READY, intent, "Intent validated.")

    def answer_intent(self, intent: QuestionIntent) -> QuestionAnswer:
        """Execute an already validated intent through allow-listed service methods."""

        validated = self.validate_intent(intent)
        if validated.status != ParseStatus.READY:
            return self._parse_failure(validated)

        spec = _SPECS[intent.metric]
        if (
            intent.metric
            in {
                MetricKey.MARKET_PRICE_GAP,
                MetricKey.FREIGHT_PER_CASE,
                MetricKey.COMPETITOR_COVERAGE,
            }
            and self.external_service is None
        ):
            integration = (
                "BazaarPulse matching and price-position metrics"
                if intent.metric
                in {MetricKey.MARKET_PRICE_GAP, MetricKey.COMPETITOR_COVERAGE}
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
        if (
            intent.metric
            in {MetricKey.WEATHER_ASSOCIATION, MetricKey.HOLIDAY_ASSOCIATION}
            and self.context_service is None
        ):
            return QuestionAnswer(
                status=AnswerStatus.INTEGRATION_REQUIRED,
                summary=(
                    "This question was understood, but the governed context snapshot and "
                    "publication gates are not available."
                ),
                interpretation=intent.interpretation(),
                definition=spec.definition,
                sources=spec.sources,
                warnings=("No association or estimate was fabricated.",),
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
            MetricKey.ALLOCATION_RATE: self._allocation_rate,
            MetricKey.POST_ALLOCATION_FULFILMENT: self._post_allocation_fulfilment,
            MetricKey.ON_TIME_RATE: self._on_time_rate,
            MetricKey.DELIVERY_ON_TIME_RATE: self._delivery_on_time_rate,
            MetricKey.POD_COVERAGE: self._pod_coverage,
            MetricKey.DELIVERY_FAILURES: self._delivery_failures,
            MetricKey.BACKLOG: self._backlog,
            MetricKey.SHORT_DELIVERY_EXPOSURE: self._short_delivery_exposure,
            MetricKey.INVENTORY_RISK: self._inventory_risk,
            MetricKey.CREDIT_NOTE_LEAKAGE: self._credit_note_leakage,
            MetricKey.COMPETITOR_COVERAGE: self._competitor_coverage,
            MetricKey.WEATHER_ASSOCIATION: self._weather_association,
            MetricKey.HOLIDAY_ASSOCIATION: self._holiday_association,
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
            if intent.ranking != Ranking.NONE:
                frame = frame.sort_values(
                    "fill_rate",
                    ascending=_ranking_ascending(intent, adverse=False),
                    na_position="last",
                )
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
            if dimension == DimensionKey.MONTH and intent.ranking == Ranking.NONE:
                summary = (
                    f"The first month shown is {first[group_column]}, at "
                    f"{_format_percent(first['fill_rate'])} fill rate."
                )
            else:
                summary = (
                    f"{first[group_column]} has the "
                    f"{_ranking_label(intent, adverse=False)} fill rate at "
                    f"{_format_percent(first['fill_rate'])}."
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
                filters, dimension, intent.quantity_basis, limit=100
            )
            previous_frame = self.metric_service.shortage_contributors(
                previous_filters, dimension, intent.quantity_basis, limit=100
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

    def _service_ratio_answer(
        self,
        intent: QuestionIntent,
        *,
        metric_key: str,
        column: str,
        title: str,
        numerator_name: str,
        denominator_name: str,
    ) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        if not intent.dimensions:
            metric = self.metric_service.executive_summary(filters, intent.quantity_basis)[
                metric_key
            ]
            evidence = EvidenceBlock(
                title,
                "fct_order_service",
                (column, numerator_name, denominator_name, "orders"),
                ((metric.value, metric.numerator, metric.denominator, metric.records),),
            )
            return self._base_answer(
                intent,
                f"{title} was {_format_percent(metric.value)}.",
                (evidence,),
            )

        dimension = intent.dimensions[0]
        if dimension == DimensionKey.MONTH:
            frame = self.metric_service.service_trend(filters, intent.quantity_basis)
            group_column = "month"
        else:
            frame = self.metric_service.service_by_dimension(
                filters,
                dimension.value,
                intent.quantity_basis,
                limit=None,
                worst_first=True,
            )
            group_column = "dimension_value"
        if dimension != DimensionKey.MONTH or intent.ranking != Ranking.NONE:
            frame = frame.sort_values(
                column,
                ascending=_ranking_ascending(intent, adverse=False),
                na_position="last",
            )
        if intent.limit is not None:
            frame = frame.head(intent.limit)
        evidence = _frame_evidence(f"{title} breakdown", "fct_order_service", frame)
        if frame.empty:
            summary = f"No {title.casefold()} groups were found."
        else:
            first = frame.iloc[0]
            if dimension == DimensionKey.MONTH and intent.ranking == Ranking.NONE:
                summary = (
                    f"The first month shown is {first[group_column]}, at "
                    f"{_format_percent(first[column])} {title.casefold()}."
                )
            else:
                summary = (
                    f"{first[group_column]} has the "
                    f"{_ranking_label(intent, adverse=False)} {title.casefold()} at "
                    f"{_format_percent(first[column])}."
                )
        return self._base_answer(intent, summary, (evidence,))

    def _allocation_rate(self, intent: QuestionIntent) -> QuestionAnswer:
        return self._service_ratio_answer(
            intent,
            metric_key="allocation_rate",
            column="allocation_rate",
            title="Allocation rate",
            numerator_name="allocated_quantity",
            denominator_name="ordered_quantity",
        )

    def _post_allocation_fulfilment(self, intent: QuestionIntent) -> QuestionAnswer:
        return self._service_ratio_answer(
            intent,
            metric_key="post_allocation_fulfilment",
            column="post_allocation_fulfilment",
            title="Post-allocation fulfilment",
            numerator_name="delivered_quantity",
            denominator_name="allocated_quantity",
        )

    def _delivery_rate_answer(
        self,
        intent: QuestionIntent,
        *,
        summary_key: str,
        column: str,
        title: str,
    ) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        if not intent.dimensions:
            metric = self.metric_service.delivery_exception_summary(filters)[summary_key]
            evidence = EvidenceBlock(
                title,
                "fct_delivery",
                (column, "numerator", "denominator", "deliveries"),
                ((metric.value, metric.numerator, metric.denominator, metric.records),),
            )
            return self._base_answer(
                intent,
                f"{title} was {_format_percent(metric.value)}.",
                (evidence,),
                warnings=("This uses the actual-delivery-date cohort.",),
            )

        dimension = intent.dimensions[0]
        if dimension == DimensionKey.MONTH:
            frame = self.metric_service.delivery_exception_trend(filters)
            group_column = "month"
        else:
            frame = self.metric_service.delivery_exceptions_by_dimension(
                filters,
                dimension.value,
                min_deliveries=25,
                limit=1_000,
            )
            group_column = "dimension_value"
        if dimension != DimensionKey.MONTH or intent.ranking != Ranking.NONE:
            frame = frame.sort_values(
                column,
                ascending=_ranking_ascending(intent, adverse=False),
                na_position="last",
            )
        if intent.limit is not None:
            frame = frame.head(intent.limit)
        evidence = _frame_evidence(f"{title} breakdown", "fct_delivery", frame)
        if frame.empty:
            summary = f"No {title.casefold()} groups were found."
        else:
            first = frame.iloc[0]
            if dimension == DimensionKey.MONTH and intent.ranking == Ranking.NONE:
                summary = (
                    f"The first month shown is {first[group_column]}, at "
                    f"{_format_percent(first[column])} {title.casefold()}."
                )
            else:
                summary = (
                    f"{first[group_column]} has the "
                    f"{_ranking_label(intent, adverse=False)} {title.casefold()} at "
                    f"{_format_percent(first[column])}."
                )
        return self._base_answer(
            intent,
            summary,
            (evidence,),
            warnings=(
                "This uses the actual-delivery-date cohort; groups below 25 deliveries are "
                "excluded from dimensional rankings.",
            ),
        )

    def _on_time_rate(self, intent: QuestionIntent) -> QuestionAnswer:
        answer = self._service_ratio_answer(
            intent,
            metric_key="on_time_rate",
            column="on_time_rate",
            title="Requested-cohort timestamp-derived on-time rate",
            numerator_name="on_time_orders",
            denominator_name="timestamp_eligible_orders",
        )
        return replace(
            answer,
            warnings=(
                *answer.warnings,
                "This service KPI uses the requested-delivery-date cohort. Ask for the "
                "actual-delivery-date on-time rate for the operational exception cohort.",
            ),
        )

    def _delivery_on_time_rate(self, intent: QuestionIntent) -> QuestionAnswer:
        return self._delivery_rate_answer(
            intent,
            summary_key="delivery_on_time_rate",
            column="on_time_rate",
            title="Timestamp-derived on-time rate",
        )

    def _pod_coverage(self, intent: QuestionIntent) -> QuestionAnswer:
        return self._delivery_rate_answer(
            intent,
            summary_key="pod_coverage_rate",
            column="pod_coverage_rate",
            title="Proof-of-delivery coverage",
        )

    def _delivery_failures(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        overall = self.metric_service.delivery_exception_summary(filters)[
            "recorded_failure_rate"
        ]
        overall_block = EvidenceBlock(
            "Recorded delivery-failure rate",
            "fct_delivery",
            ("recorded_failure_rate", "failure_deliveries", "deliveries"),
            ((overall.value, overall.numerator, overall.denominator),),
        )
        dimension = intent.dimensions[0] if intent.dimensions else DimensionKey.FAILURE_REASON
        if dimension == DimensionKey.FAILURE_REASON:
            frame = self.metric_service.failure_reason_pareto(
                filters, limit=1_000
            )
            frame = frame.sort_values(
                "failure_deliveries",
                ascending=_ranking_ascending(intent, adverse=True),
                na_position="last",
            ).head(intent.limit or 20)
            title = "Recorded delivery-failure labels"
        elif dimension == DimensionKey.MONTH:
            frame = self.metric_service.delivery_exception_trend(filters).sort_values(
                "recorded_failure_rate",
                ascending=_ranking_ascending(intent, adverse=True),
                na_position="last",
            )
            if intent.limit is not None:
                frame = frame.head(intent.limit)
            title = "Recorded delivery failures by month"
        else:
            frame = self.metric_service.delivery_exceptions_by_dimension(
                filters,
                dimension.value,
                min_deliveries=25,
                limit=1_000,
            ).sort_values(
                "recorded_failure_rate",
                ascending=_ranking_ascending(intent, adverse=True),
                na_position="last",
            )
            if intent.limit is not None:
                frame = frame.head(intent.limit)
            title = f"Recorded delivery failures by {dimension.value}"
        detail_block = _frame_evidence(title, "fct_delivery", frame)
        if frame.empty:
            summary = (
                f"Recorded delivery-failure rate was {_format_percent(overall.value)}; "
                "no grouped failure evidence was found."
            )
        else:
            summary = f"Recorded delivery-failure rate was {_format_percent(overall.value)}."
            if dimension == DimensionKey.FAILURE_REASON:
                summary += (
                    f" {frame.iloc[0]['failure_reason_code']} has the "
                    f"{_ranking_label(intent, adverse=True)} recorded count, with "
                    f"{int(frame.iloc[0]['failure_deliveries']):,} deliveries."
                )
        return self._base_answer(
            intent,
            summary,
            (overall_block, detail_block),
            warnings=(
                "Failure labels are recorded source signals; they do not establish root cause "
                "or responsibility.",
            ),
        )

    def _backlog(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        if not intent.dimensions:
            metric = self.metric_service.executive_summary(filters, intent.quantity_basis)[
                "overdue_backlog_orders"
            ]
            evidence = EvidenceBlock(
                "Current overdue OPEN-order backlog",
                "fct_order_service",
                ("as_of_date", "overdue_open_orders"),
                ((intent.period.end, metric.value),),
            )
            return self._base_answer(
                intent,
                f"There were {_format_number(metric.value)} currently OPEN eligible orders "
                f"due on or before {intent.period.end.isoformat()}.",
                (evidence,),
                warnings=(
                    "Backlog uses the current source order status and is not a historical "
                    "status reconstruction.",
                ),
            )

        dimension = intent.dimensions[0]
        frame = self.metric_service.backlog_by_dimension(
            filters,
            dimension.value,
            intent.quantity_basis,
            limit=1_000,
        )
        frame = frame.sort_values(
            "overdue_orders",
            ascending=_ranking_ascending(intent, adverse=True),
            na_position="last",
        ).head(intent.limit or 30)
        evidence = _frame_evidence("Current overdue backlog breakdown", "fct_order_service", frame)
        summary = (
            "No overdue OPEN-order groups were found."
            if frame.empty
            else f"{frame.iloc[0]['dimension_value']} has the "
            f"{_ranking_label(intent, adverse=True)} current backlog with "
            f"{int(frame.iloc[0]['overdue_orders']):,} overdue OPEN orders."
        )
        return self._base_answer(
            intent,
            summary,
            (evidence,),
            warnings=(
                "Backlog uses the current source order status and is not a historical status "
                "reconstruction.",
            ),
        )

    def _short_delivery_exposure(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        if not intent.dimensions:
            metric = self.metric_service.executive_summary(filters, intent.quantity_basis)[
                "short_delivery_value_exposure_inr"
            ]
            evidence = EvidenceBlock(
                "Short-delivery booked-value exposure",
                "fct_order_line",
                ("exposure_inr", "short_lines"),
                ((metric.value, metric.records),),
            )
            return self._base_answer(
                intent,
                f"Short-delivery booked-value exposure was ₹{_format_number(metric.value)}.",
                (evidence,),
                warnings=("This is exposure, not accounting loss, margin, profit, or cash.",),
            )
        dimension = intent.dimensions[0]
        frame = self.metric_service.short_delivery_exposure(
            filters,
            dimension.value,
            intent.quantity_basis,
            limit=intent.limit or 30,
        )
        frame = frame.sort_values(
            "short_delivery_value_exposure_inr",
            ascending=intent.ranking == Ranking.BEST,
            na_position="last",
        )
        evidence = _frame_evidence(
            "Short-delivery booked-value exposure breakdown", "fct_order_line", frame
        )
        summary = (
            "No short-delivery exposure groups were found."
            if frame.empty
            else f"{frame.iloc[0]['dimension_value']} has the "
            f"{'smallest' if intent.ranking == Ranking.BEST else 'largest'} exposure at ₹"
            f"{_format_number(frame.iloc[0]['short_delivery_value_exposure_inr'])}."
        )
        return self._base_answer(
            intent,
            summary,
            (evidence,),
            warnings=("This is exposure, not accounting loss, margin, profit, or cash.",),
        )

    def _inventory_risk(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        if not intent.dimensions:
            metric = self.metric_service.executive_summary(filters)["near_expiry_cases"]
            evidence = EvidenceBlock(
                "Near-expiry available cases",
                "fct_inventory_snapshot",
                ("as_of_date", "near_expiry_cases"),
                ((intent.period.end, metric.value),),
            )
            return self._base_answer(
                intent,
                f"The latest eligible inventory snapshot contains "
                f"{_format_number(metric.value)} near-expiry available cases.",
                (evidence,),
                warnings=(
                    "Inventory uses the latest weekly snapshot on or before the period end and "
                    "cannot apply customer, route, outlet, channel, promotion, or source filters.",
                ),
            )
        dimension = intent.dimensions[0]
        frame = self.metric_service.inventory_risk(filters, dimension.value)
        frame = frame.sort_values(
            "near_expiry_cases",
            ascending=intent.ranking == Ranking.BEST,
            na_position="last",
        )
        if intent.limit is not None:
            frame = frame.head(intent.limit)
        evidence = _frame_evidence("Inventory-risk breakdown", "fct_inventory_snapshot", frame)
        summary = (
            "No inventory-risk groups were found."
            if frame.empty
            else f"{frame.iloc[0]['dimension_value']} has the "
            f"{'smallest' if intent.ranking == Ranking.BEST else 'largest'} near-expiry "
            "exposure "
            f"with {_format_number(frame.iloc[0]['near_expiry_cases'])} available cases."
        )
        ignored = tuple(frame.attrs.get("ignored_filters", ()))
        warnings = [
            "Inventory uses the latest weekly snapshot on or before the selected period end."
        ]
        if ignored:
            warnings.append("Inventory cannot apply active filter(s): " + ", ".join(ignored) + ".")
        return self._base_answer(intent, summary, (evidence,), warnings=tuple(warnings))

    def _credit_note_leakage(self, intent: QuestionIntent) -> QuestionAnswer:
        filters = intent.filters.to_metric_filters(intent.period)
        metric = self.metric_service.executive_summary(filters)["approved_credit_note_rate"]
        rate_block = EvidenceBlock(
            "Approved credit-note leakage rate",
            "fct_return_credit_note + fct_order_line",
            ("rate", "approved_credit_value_inr", "estimated_delivered_value_inr"),
            ((metric.value, metric.numerator, metric.denominator),),
        )
        blocks: list[EvidenceBlock] = [rate_block]
        if intent.dimensions:
            dimension = intent.dimensions[0]
            if dimension == DimensionKey.DISPOSITION:
                frame = self.metric_service.return_disposition_summary(filters)
                value_column = "approved_credit_note_value_inr"
            else:
                frame = self.metric_service.returns_by_dimension(
                    filters,
                    dimension.value,
                    statuses=("APPROVED",),
                    limit=1_000,
                )
                value_column = "credit_note_value_inr"
            if value_column in frame:
                frame = frame.sort_values(
                    value_column,
                    ascending=_ranking_ascending(intent, adverse=True),
                    na_position="last",
                )
            frame = frame.head(intent.limit or 20)
            blocks.append(
                _frame_evidence(
                    f"Approved credit-note value by {dimension.value}",
                    "fct_return_credit_note",
                    frame,
                )
            )
        status = self.metric_service.credit_status_summary(filters)
        blocks.append(
            _frame_evidence("Credit-note workflow status", "fct_return_credit_note", status)
        )
        return self._base_answer(
            intent,
            f"Approved credit-note leakage rate was {_format_percent(metric.value)}, with "
            f"₹{_format_number(metric.numerator)} in approved credit-note value.",
            tuple(blocks),
            warnings=(
                "The numerator includes all approved source credit notes matching return filters; "
                "the denominator includes only eligible completed-service dispatch estimates. "
                "They are independent populations on return and requested-delivery dates. Grouped "
                "evidence is value, not a grouped rate; none of these measures is accounting "
                "profit or cash recovery.",
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
        if dimension != DimensionKey.MONTH or intent.ranking != Ranking.NONE:
            frame = frame.sort_values(
                "strict_otif_rate",
                ascending=_ranking_ascending(intent, adverse=False),
                na_position="last",
            )
        if intent.limit is not None:
            frame = frame.head(intent.limit)
        evidence = _frame_evidence("Strict OTIF breakdown", "fct_order_service", frame)
        if frame.empty:
            summary = "No OTIF groups were found."
        else:
            rates = frame["strict_otif_rate"].dropna()
            dimension_label = dimension.value.replace("_", " ")
            if not rates.empty and rates.nunique() == 1:
                summary = (
                    f"Strict OTIF is {_format_percent(rates.iloc[0])} for every "
                    f"{dimension_label} shown."
                )
            else:
                group = frame.iloc[0].get("dimension_value", frame.iloc[0].get("month"))
                if dimension == DimensionKey.MONTH and intent.ranking == Ranking.NONE:
                    summary = (
                        f"The first month shown is {group}, at "
                        f"{_format_percent(frame.iloc[0]['strict_otif_rate'])} strict OTIF."
                    )
                else:
                    summary = (
                        f"The {_ranking_label(intent, adverse=False)} strict OTIF is "
                        f"{_format_percent(frame.iloc[0]['strict_otif_rate'])} for {group}."
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
                limit=1_000,
            )
            if "credit_note_value_inr" in frame:
                frame = frame.sort_values(
                    "credit_note_value_inr",
                    ascending=_ranking_ascending(intent, adverse=True),
                    na_position="last",
                )
            frame = frame.head(intent.limit or 20)
            evidence.append(
                _frame_evidence(
                    f"Approved returns by {dimension.value}",
                    "fct_return_credit_note",
                    frame,
                )
            )
        ranked_evidence = tuple(evidence)
        status = self.metric_service.credit_status_summary(filters)
        evidence.append(
            _frame_evidence(
                "Credit-note workflow status",
                "fct_return_credit_note",
                status,
            )
        )
        first_nonempty = next((block for block in ranked_evidence if block.rows), None)
        if first_nonempty is None:
            summary = "No approved return evidence was found."
        else:
            row = first_nonempty.rows[0]
            amount_index = first_nonempty.columns.index("credit_note_value_inr")
            summary = (
                f"The {_ranking_label(intent, adverse=True)} "
                f"{first_nonempty.title.removeprefix('Approved returns by ')} is "
                f"{row[0]} with ₹{_format_number(row[amount_index])} in approved credit notes."
            )
        return self._base_answer(
            intent,
            summary,
            tuple(evidence),
            warnings=(
                "Only APPROVED credit notes drive the ranking and headline value; PENDING and "
                "REJECTED rows are shown separately as workflow evidence.",
            ),
        )

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
            available = [row for row in rows if row[1] is not None]
            if intent.ranking != Ranking.NONE:
                unavailable = [row for row in rows if row[1] is None]
                available.sort(
                    key=lambda row: _as_float(row[1]),
                    reverse=not _ranking_ascending(intent, adverse=True),
                )
                rows = [*available, *unavailable]
            if intent.limit is not None:
                rows = rows[: intent.limit]
            evidence = EvidenceBlock(
                "Chilled excursions by month",
                "fct_delivery",
                ("month", "excursions_per_100", "excursions", "chilled_deliveries"),
                tuple(rows),
            )
            if available:
                leading = (
                    available[0]
                    if intent.ranking != Ranking.NONE
                    else max(available, key=lambda row: _as_float(row[1]))
                )
                direction = (
                    _ranking_label(intent, adverse=True)
                    if intent.ranking != Ranking.NONE
                    else "highest"
                )
                summary = (
                    f"The {direction} monthly "
                    f"chilled-excursion rate was {_format_number(leading[1])} per 100 "
                    f"in {leading[0]}."
                )
            else:
                summary = "No monthly chilled-delivery denominator was available."
            return self._base_answer(intent, summary, (evidence,))

        if intent.dimensions:
            dimension = intent.dimensions[0]
            frame = self.metric_service.cold_chain_by_dimension(
                filters,
                dimension.value,
                min_chilled_deliveries=RANKED_COLD_CHAIN_MIN_DELIVERIES,
                limit=1_000,
            )
            frame = frame.sort_values(
                "excursions_per_100",
                ascending=_ranking_ascending(intent, adverse=True),
                na_position="last",
            ).head(intent.limit or 20)
            evidence = _frame_evidence("Chilled excursion breakdown", "fct_delivery", frame)
            if frame.empty:
                summary = "No chilled-delivery groups were found."
            else:
                leading = frame.iloc[0]
                chilled_deliveries = int(leading["chilled_deliveries"])
                summary = (
                    f"{leading['dimension_value']} has the "
                    f"{_ranking_label(intent, adverse=True)} rate at "
                    f"{_format_number(leading['excursions_per_100'])} per 100 across "
                    f"{chilled_deliveries:,} chilled deliveries."
                )
            return self._base_answer(
                intent,
                summary,
                (evidence,),
                warnings=(
                    "Ranked dimensional cold-chain results exclude groups with fewer than "
                    f"{RANKED_COLD_CHAIN_MIN_DELIVERIES} eligible chilled deliveries. This "
                    "changes inclusion only; the source-flag excursion formula is unchanged.",
                ),
            )

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
        minimum_deliveries = 25
        frame = self.metric_service.delivery_exceptions_by_dimension(
            filters,
            "route",
            min_deliveries=minimum_deliveries,
            limit=1_000,
        )
        qualifying = frame.loc[frame["late_over_2h_rate"] > intent.rate_threshold].sort_values(
            "late_over_2h_rate",
            ascending=_ranking_ascending(intent, adverse=True),
        )
        if intent.limit is not None:
            qualifying = qualifying.head(intent.limit)
        evidence = _frame_evidence(
            "Routes above the late-delivery threshold",
            "fct_delivery",
            qualifying,
        )
        if qualifying.empty:
            summary = (
                "No route with at least "
                f"{minimum_deliveries} actual-date deliveries exceeded the interpreted "
                "late-delivery threshold."
            )
        else:
            first = qualifying.iloc[0]
            summary = (
                f"{len(qualifying)} route(s) exceeded the threshold; "
                f"{first['dimension_value']} was {_ranking_label(intent, adverse=True)} at "
                f"{_format_percent(first['late_over_2h_rate'])} among groups with at least "
                f"{minimum_deliveries} actual-date deliveries."
            )
        return self._base_answer(
            intent,
            summary,
            (evidence,),
            warnings=(
                f"Routes below {minimum_deliveries} actual-date deliveries are excluded from "
                "this ranking.",
            ),
        )

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
        requested_limit = intent.limit or 20
        frame = self.external_service.competitor_price_gap(
            filters,
            city=city,
            category=category,
            top_n=100 if intent.ranking != Ranking.NONE else requested_limit,
        )
        unavailable_reason = frame.attrs.get("unavailable_reason")
        if frame.empty and unavailable_reason:
            spec = _SPECS[intent.metric]
            coverage_warning = frame.attrs.get("coverage_warning")
            return QuestionAnswer(
                status=AnswerStatus.INTEGRATION_REQUIRED,
                summary=str(unavailable_reason),
                interpretation=intent.interpretation(),
                definition=spec.definition,
                sources=spec.sources,
                warnings=(str(coverage_warning),) if coverage_warning else (),
                intent=intent,
            )
        if intent.ranking != Ranking.NONE:
            gap_column = (
                "unit_price_gap_inr"
                if "unit_price_gap_inr" in frame
                else "price_gap_inr"
            )
            if gap_column in frame:
                frame = frame.sort_values(
                    gap_column,
                    ascending=_ranking_ascending(intent, adverse=True),
                    na_position="last",
                ).head(requested_limit)
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

    def _competitor_coverage(self, intent: QuestionIntent) -> QuestionAnswer:
        if self.external_service is None:
            raise RuntimeError("External metrics were not supplied")
        frame = self.external_service.competitor_match_quality()
        if intent.filters.cities and "city" in frame:
            frame = frame.loc[frame["city"].isin(intent.filters.cities)].copy()
        evidence = _frame_evidence(
            "Governed competitor-match outcomes",
            "BazaarPulse current listings and match decisions",
            frame,
        )
        total = int(frame["listings"].sum()) if "listings" in frame else 0
        matched = (
            int(frame.loc[frame["match_status"] == "matched", "listings"].sum())
            if {"match_status", "listings"}.issubset(frame.columns)
            else 0
        )
        coverage = matched / total if total else None
        scope = (
            " in " + ", ".join(intent.filters.cities)
            if intent.filters.cities
            else " source-wide"
        )
        summary = (
            "No current competitor-match evidence is available."
            if total == 0
            else f"Governed competitor-match coverage{scope} is {_format_percent(coverage)} "
            f"({matched:,} of {total:,} current listings)."
        )
        return self._base_answer(
            intent,
            summary,
            (evidence,),
            warnings=(
                "Coverage is a latest entity-resolution measure; operational reporting-period "
                "filters do not apply. It is not sales coverage, price availability, or a "
                "live-price claim.",
            ),
        )

    def _context_association(
        self, intent: QuestionIntent, *, weather: bool
    ) -> QuestionAnswer:
        if self.context_service is None:
            raise RuntimeError("Context metrics were not supplied")
        filters = intent.filters.to_metric_filters(intent.period)
        result = (
            self.context_service.weather_delivery_association(filters)
            if weather
            else self.context_service.holiday_service_association(filters)
        )
        gate = result.gate
        if not gate.publishable:
            spec = _SPECS[intent.metric]
            reasons = "; ".join(gate.reasons) or "publication gates did not pass"
            return QuestionAnswer(
                status=AnswerStatus.INTEGRATION_REQUIRED,
                summary=f"The association is withheld because {reasons}.",
                interpretation=intent.interpretation(),
                definition=spec.definition,
                sources=spec.sources,
                warnings=(gate.disclosure,),
                intent=intent,
            )
        frame = result.frame
        label = "Weather" if weather else "Holiday"
        evidence = _frame_evidence(
            f"{label} service association",
            gate.source_name,
            frame,
        )
        summary = f"Published {len(frame)} governed {label.casefold()} comparison cohort(s)."
        return self._base_answer(
            intent,
            summary,
            (evidence,),
            warnings=(gate.disclosure,),
        )

    def _weather_association(self, intent: QuestionIntent) -> QuestionAnswer:
        return self._context_association(intent, weather=True)

    def _holiday_association(self, intent: QuestionIntent) -> QuestionAnswer:
        return self._context_association(intent, weather=False)

    def _freight_per_case(self, intent: QuestionIntent) -> QuestionAnswer:
        if self.external_service is None:
            raise RuntimeError("External metrics were not supplied")
        filters = intent.filters.to_metric_filters(intent.period)
        by_route = DimensionKey.ROUTE in intent.dimensions
        frame = (
            self.external_service.freight_by_route(filters)
            if by_route
            else self.external_service.freight_by_warehouse(filters)
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
        settled_column = "settled_freight_cost_per_delivered_case_inr"
        if settled_column in frame:
            frame = frame.sort_values(
                settled_column,
                ascending=_ranking_ascending(intent, adverse=True),
                na_position="last",
            )
        if intent.limit is not None:
            frame = frame.head(intent.limit)
        evidence = _frame_evidence(
            "Settled/paid freight per delivered case by "
            + ("route" if by_route else "warehouse"),
            "partner freight invoices",
            frame,
        )
        if frame.empty:
            summary = "No freight and delivered-case evidence was found."
        else:
            ranked = frame.dropna(subset=[settled_column])
            if ranked.empty:
                summary = "No settled/paid freight and delivered-case ratio was available."
            else:
                first = ranked.iloc[0]
                dimension_column = "route_code" if by_route else "warehouse_code"
                summary = (
                    f"{first[dimension_column]} has the "
                    f"{_ranking_label(intent, adverse=True)} settled/paid freight per "
                    f"delivered case at ₹{_format_number(first[settled_column])}."
                )
        coverage_warning = frame.attrs.get("coverage_warning")
        attribution = frame.attrs.get("attribution")
        warnings_list = [str(coverage_warning)] if coverage_warning else []
        if attribution:
            warnings_list.append(str(attribution))
        ignored_filters = tuple(frame.attrs.get("ignored_filters", ()))
        if ignored_filters:
            warnings_list.append(
                "Freight invoices and delivered-case denominators cannot apply the active "
                "filter(s): " + ", ".join(str(value) for value in ignored_filters) + "."
            )
        warnings = tuple(warnings_list)
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
