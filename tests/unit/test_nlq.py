from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from kestrel.metrics.service import FilterSet, MetricValue, QuantityBasis
from kestrel.nlq import (
    AnswerStatus,
    ConversationMemory,
    DimensionKey,
    MetricKey,
    ParseStatus,
    QuestionRouter,
    Ranking,
    answer_question,
    normalize_business_spelling,
)
from kestrel.nlq.router import _format_percent
from kestrel.nlq.semantic import (
    ModelProvenance,
    SemanticResolution,
    SemanticStatus,
    load_intent_catalog,
)


def test_nlq_percent_format_matches_dashboard_precision() -> None:
    assert _format_percent(0.0003330327) == "0.033%"
    assert _format_percent(0.0000001) == "<0.001%"
    assert _format_percent(0.0) == "0.0%"


class FakeSemanticResolver:
    def __init__(self, resolution: SemanticResolution) -> None:
        self.resolution = resolution
        self.questions: list[str] = []

    def resolve(
        self,
        question: str,
        *,
        allowed_intents: object = None,
    ) -> SemanticResolution:
        del allowed_intents
        self.questions.append(question)
        return self.resolution


class CatalogSemanticResolver:
    """Deterministic test double that models a perfect catalogue embedding match."""

    def __init__(self) -> None:
        self.expected = {
            example.casefold(): intent.key
            for intent in load_intent_catalog().intents
            for example in intent.examples
        }

    def resolve(
        self,
        question: str,
        *,
        allowed_intents: object = None,
    ) -> SemanticResolution:
        del allowed_intents
        intent = self.expected.get(question.casefold())
        return _semantic_resolution(
            SemanticStatus.READY if intent else SemanticStatus.UNSUPPORTED,
            intent,
            message="catalogue test resolution",
        )


def _semantic_resolution(
    status: SemanticStatus,
    intent: str | None,
    *,
    message: str = "semantic result",
) -> SemanticResolution:
    return SemanticResolution(
        status=status,
        intent_key=intent,
        confidence=0.82,
        runner_up_intent_key="fill_rate",
        runner_up_confidence=0.55,
        margin=0.27,
        matched_example="How much stock was assigned?",
        runner_up_example="Where is fulfilment weak?",
        provenance=ModelProvenance("fake", "test", "python"),
        message=message,
    )


class FakeMetricService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def available_date_range(
        self, date_basis: str = "requested_delivery"
    ) -> tuple[date, date]:
        self.calls.append(("available_date_range", date_basis))
        maximum = {
            "actual_delivery": date(2026, 7, 4),
            "returns": date(2026, 7, 29),
            "inventory": date(2026, 7, 5),
        }.get(date_basis, date(2026, 6, 30))
        return date(2025, 1, 1), maximum

    def filter_options(self) -> dict[str, list[str]]:
        self.calls.append(("filter_options", None))
        return {
            "customer_regions": ["Central", "East", "North", "South", "West"],
            "warehouse_regions": ["Central", "East", "North", "South", "West"],
            "warehouse_codes": ["WH01", "WH02"],
            "route_codes": ["RT0001", "RT0002"],
            "outlet_codes": ["OUT00001", "OUT00002"],
            "channels": ["GT", "MT", "HORECA", "ECOM_DARKSTORE"],
            "categories": ["Dairy", "Frozen", "Snacks"],
        }

    def executive_summary(
        self, filters: FilterSet, basis: QuantityBasis = QuantityBasis.EACHES
    ) -> dict[str, MetricValue]:
        self.calls.append(("executive_summary", (filters, basis)))
        monthly_rate = {4: 4.0, 5: 6.0, 6: 2.0}.get(filters.start_date.month, 3.0)
        return {
            "fill_rate": MetricValue("fill_rate", 0.9, 900.0, 1000.0, "percent", 10),
            "allocation_rate": MetricValue("allocation_rate", 0.95, 950.0, 1000.0, "percent", 10),
            "post_allocation_fulfilment": MetricValue(
                "post_allocation_fulfilment", 900 / 950, 900.0, 950.0, "percent", 10
            ),
            "strict_otif": MetricValue("strict_otif", 0.0, 0.0, 10.0, "percent", 10),
            "overdue_backlog_orders": MetricValue(
                "overdue_backlog_orders", 3.0, 3.0, None, "orders", 3
            ),
            "short_delivery_value_exposure_inr": MetricValue(
                "short_delivery_value_exposure_inr",
                42500.0,
                42500.0,
                None,
                "INR",
                8,
            ),
            "near_expiry_cases": MetricValue("near_expiry_cases", 125.0, 125.0, None, "cases", 2),
            "approved_credit_note_rate": MetricValue(
                "approved_credit_note_rate", 0.025, 25000.0, 1000000.0, "percent", 5
            ),
            "temperature_excursions_per_100": MetricValue(
                "temperature_excursions_per_100",
                monthly_rate,
                monthly_rate,
                100.0,
                "per_100",
                100,
            ),
        }

    def service_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int | None = None,
        worst_first: bool = True,
    ) -> pd.DataFrame:
        self.calls.append(
            (
                "service_by_dimension",
                (filters, dimension, basis, limit, worst_first),
            )
        )
        if dimension == "warehouse":
            return pd.DataFrame(
                {
                    "dimension_value": ["WH01", "WH02"],
                    "orders": [10, 20],
                    "ordered_quantity": [100.0, 200.0],
                    "delivered_quantity": [80.0, 190.0],
                    "short_quantity": [20.0, 10.0],
                    "allocation_rate": [0.82, 0.97],
                    "post_allocation_fulfilment": [0.98, 0.96],
                    "fill_rate": [0.80, 0.95],
                    "on_time_rate": [0.40, 0.50],
                    "strict_otif_rate": [0.0, 0.0],
                    "late_over_2h_rate": [0.05, 0.20],
                }
            )
        if dimension == "customer_region":
            return pd.DataFrame(
                {
                    "dimension_value": ["West", "South"],
                    "orders": [30, 40],
                    "ordered_quantity": [300.0, 400.0],
                    "delivered_quantity": [270.0, 380.0],
                    "short_quantity": [30.0, 20.0],
                    "fill_rate": [0.90, 0.95],
                    "on_time_rate": [0.40, 0.50],
                    "strict_otif_rate": [0.0, 0.0],
                    "late_over_2h_rate": [0.10, 0.05],
                }
            )
        if dimension == "route":
            return pd.DataFrame(
                {
                    "dimension_value": ["RT0001", "RT0002"],
                    "orders": [20, 10],
                    "ordered_quantity": [200.0, 100.0],
                    "delivered_quantity": [190.0, 80.0],
                    "short_quantity": [10.0, 20.0],
                    "fill_rate": [0.95, 0.80],
                    "on_time_rate": [0.60, 0.30],
                    "strict_otif_rate": [0.0, 0.0],
                    "late_over_2h_rate": [0.05, 0.20],
                }
            )
        raise AssertionError(f"Unexpected service dimension: {dimension}")

    def delivery_exceptions_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        min_deliveries: int = 50,
        limit: int = 30,
    ) -> pd.DataFrame:
        self.calls.append(
            (
                "delivery_exceptions_by_dimension",
                (filters, dimension, min_deliveries, limit),
            )
        )
        assert dimension == "route"
        frame = pd.DataFrame(
            {
                "dimension_value": ["RT0001", "RT0002", "RT0003"],
                "deliveries": [100, 25, 24],
                "on_time_rate": [0.90, 0.60, 0.0],
                "late_over_2h_rate": [0.05, 0.20, 1.0],
                "pod_coverage_rate": [0.95, 0.80, 0.0],
                "recorded_failures": [2, 5, 24],
                "recorded_failure_rate": [0.02, 0.20, 1.0],
            }
        )
        return frame.loc[frame["deliveries"] >= min_deliveries].head(limit)

    def service_trend(
        self, filters: FilterSet, basis: QuantityBasis = QuantityBasis.EACHES
    ) -> pd.DataFrame:
        self.calls.append(("service_trend", (filters, basis)))
        return pd.DataFrame(
            {
                "month": [date(2026, 4, 1), date(2026, 5, 1)],
                "orders": [10, 20],
                "allocation_rate": [0.85, 0.95],
                "post_allocation_fulfilment": [0.94, 0.96],
                "fill_rate": [0.8, 0.9],
                "on_time_rate": [0.4, 0.5],
                "strict_otif_rate": [0.0, 0.0],
                "short_quantity": [20.0, 10.0],
            }
        )

    def backlog_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int = 30,
    ) -> pd.DataFrame:
        self.calls.append(("backlog_by_dimension", (filters, dimension, basis, limit)))
        return pd.DataFrame(
            {
                "dimension_value": ["WH02", "WH01"],
                "overdue_orders": [3, 1],
                "overdue_ordered_quantity": [200.0, 50.0],
                "oldest_requested_delivery_date": [
                    date(2026, 4, 1),
                    date(2026, 4, 8),
                ],
                "oldest_overdue_days": [90, 83],
            }
        ).head(limit)

    def delivery_exception_summary(self, filters: FilterSet) -> dict[str, MetricValue]:
        self.calls.append(("delivery_exception_summary", filters))
        return {
            "delivery_on_time_rate": MetricValue(
                "delivery_on_time_rate", 0.8, 80.0, 100.0, "percent", 100
            ),
            "pod_coverage_rate": MetricValue(
                "pod_coverage_rate", 0.92, 92.0, 100.0, "percent", 100
            ),
            "recorded_failure_rate": MetricValue(
                "recorded_failure_rate", 0.06, 6.0, 100.0, "percent", 100
            ),
        }

    def delivery_exception_trend(self, filters: FilterSet) -> pd.DataFrame:
        self.calls.append(("delivery_exception_trend", filters))
        return pd.DataFrame(
            {
                "month": [date(2026, 4, 1), date(2026, 5, 1)],
                "deliveries": [50, 50],
                "on_time_rate": [0.75, 0.85],
                "pod_coverage_rate": [0.90, 0.94],
                "recorded_failure_rate": [0.08, 0.04],
            }
        )

    def failure_reason_pareto(self, filters: FilterSet, *, limit: int = 20) -> pd.DataFrame:
        self.calls.append(("failure_reason_pareto", (filters, limit)))
        return pd.DataFrame(
            {
                "failure_reason_code": ["DF01", "DF02"],
                "failure_deliveries": [4, 2],
                "share_of_failures": [2 / 3, 1 / 3],
                "cumulative_share": [2 / 3, 1.0],
            }
        ).head(limit)

    def short_delivery_exposure(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int = 30,
    ) -> pd.DataFrame:
        self.calls.append(("short_delivery_exposure", (filters, dimension, basis, limit)))
        return pd.DataFrame(
            {
                "dimension_value": ["WH02", "WH01"],
                "short_lines": [5, 3],
                "affected_orders": [5, 3],
                "short_quantity": [50.0, 30.0],
                "booked_line_value_inr": [200000.0, 150000.0],
                "short_delivery_value_exposure_inr": [30000.0, 12500.0],
                "exposure_share_of_booked_value": [0.15, 1 / 12],
                "allocation_short_value_exposure_inr": [18000.0, 7500.0],
                "post_allocation_short_value_exposure_inr": [12000.0, 5000.0],
            }
        ).head(limit)

    def inventory_risk(self, filters: FilterSet, dimension: str = "warehouse") -> pd.DataFrame:
        self.calls.append(("inventory_risk", (filters, dimension)))
        frame = pd.DataFrame(
            {
                "dimension_value": ["WH02", "WH01"],
                "available_cases": [200.0, 100.0],
                "near_expiry_cases": [100.0, 25.0],
                "expired_available_cases": [10.0, 0.0],
                "damaged_cases": [4.0, 1.0],
                "blocked_cases": [3.0, 0.0],
            }
        )
        if filters.customer_regions:
            frame.attrs["ignored_filters"] = ("customer region",)
        return frame

    def return_disposition_summary(
        self,
        filters: FilterSet,
        *,
        statuses: tuple[str, ...] = ("APPROVED", "PENDING", "REJECTED"),
    ) -> pd.DataFrame:
        self.calls.append(("return_disposition_summary", (filters, statuses)))
        return pd.DataFrame(
            {
                "disposition": ["SCRAP", "RESTOCK"],
                "credit_note_lines": [3, 2],
                "approved_credit_note_value_inr": [9000.0, 3500.0],
            }
        )

    def cold_chain_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        min_chilled_deliveries: int = 1,
        limit: int = 20,
    ) -> pd.DataFrame:
        self.calls.append(
            (
                "cold_chain_by_dimension",
                (filters, dimension, min_chilled_deliveries, limit),
            )
        )
        frame = pd.DataFrame(
            {
                "dimension_value": ["WH01", "WH02"],
                "chilled_deliveries": [100, 100],
                "excursions": [5, 2],
                "excursions_per_100": [5.0, 2.0],
            }
        )
        return frame.loc[
            frame["chilled_deliveries"] >= min_chilled_deliveries
        ].head(limit)

    def returns_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        statuses: tuple[str, ...] = ("APPROVED",),
        limit: int = 20,
    ) -> pd.DataFrame:
        self.calls.append(("returns_by_dimension", (filters, dimension, statuses, limit)))
        values = ["Dairy", "Snacks"] if dimension == "category" else ["RT06", "RT07"]
        return pd.DataFrame(
            {
                "dimension_value": values,
                "credit_note_lines": [5, 2],
                "return_eaches": [50.0, 20.0],
                "return_case_equivalents": [5.0, 2.0],
                "credit_note_value_inr": [12500.0, 3500.0],
            }
        ).head(limit)

    def credit_status_summary(self, filters: FilterSet) -> pd.DataFrame:
        self.calls.append(("credit_status_summary", filters))
        return pd.DataFrame(
            {
                "credit_note_status": ["APPROVED", "PENDING", "REJECTED"],
                "credit_note_lines": [5, 2, 1],
                "credit_note_value_inr": [12500.0, 3000.0, 800.0],
            }
        )

    def discontinued_order_evidence(self, filters: FilterSet, *, limit: int = 100) -> pd.DataFrame:
        self.calls.append(("discontinued_order_evidence", (filters, limit)))
        return pd.DataFrame(
            {
                "order_number": ["SO-1"],
                "order_date": [date(2026, 5, 3)],
                "outlet_code": ["OUT00001"],
                "sku_code": ["SKU-1"],
                "discontinued_date": [date(2026, 5, 1)],
                "ordered_eaches": [12.0],
            }
        )

    def shortage_contributors(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int = 20,
    ) -> pd.DataFrame:
        self.calls.append(
            ("shortage_contributors", (filters, dimension, basis, limit))
        )
        labels = {
            "short_reason": ["SR01", "SR02"],
            "category": ["Dairy", "Snacks"],
            "warehouse": ["WH01", "WH02"],
        }[dimension]
        current = filters.start_date >= date(2026, 4, 1)
        return pd.DataFrame(
            {
                "dimension_value": labels,
                "short_eaches": [30.0, 10.0] if current else [20.0, 12.0],
                "short_case_equivalents": [3.0, 1.0] if current else [2.0, 1.2],
            }
        ).head(limit)


class FakeExternalMetricService:
    def freight_by_warehouse(self, filters: FilterSet) -> pd.DataFrame:
        frame = pd.DataFrame(
            {
                "warehouse_code": ["WH02", "WH01"],
                "freight_cost_inr": [2000.0, 1000.0],
                "delivered_case_equivalents": [50.0, 50.0],
                "settled_freight_cost_per_delivered_case_inr": [25.0, 18.0],
                "freight_cost_per_delivered_case_inr": [40.0, 20.0],
            }
        )
        frame.attrs["attribution"] = "Aggregated independently at service-period × warehouse."
        return frame

    def freight_by_route(self, filters: FilterSet) -> pd.DataFrame:
        frame = pd.DataFrame(
            {
                "route_code": ["RT0002", "RT0001"],
                "freight_cost_inr": [900.0, 600.0],
                "delivered_case_equivalents": [30.0, 30.0],
                "settled_freight_cost_per_delivered_case_inr": [20.0, 15.0],
                "freight_cost_per_delivered_case_inr": [30.0, 20.0],
            }
        )
        frame.attrs["attribution"] = "Aggregated independently at service-period × route."
        return frame

    def competitor_price_gap(
        self,
        filters: FilterSet,
        *,
        city: str = "Mumbai",
        category: str | None = None,
        top_n: int = 20,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "sku_code": ["SKU-1", "SKU-2"],
                "dispatch_value_inr": [1000.0, 900.0],
                "kestrel_mrp_inr": [100.0, 80.0],
                "lowest_competitor_price_inr": [90.0, None],
                "price_gap_inr": [10.0, None],
                "latest_observation_date": [date(2026, 6, 27), None],
            }
        ).head(top_n)

    def competitor_match_quality(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "city": ["Mumbai", "Mumbai", "Delhi"],
                "match_status": ["matched", "unmatched", "matched"],
                "listings": [80, 20, 30],
            }
        )


def _router() -> tuple[QuestionRouter, FakeMetricService]:
    service = FakeMetricService()
    return QuestionRouter(service), service


def test_fill_rate_by_warehouse_parses_filters_basis_and_relative_month() -> None:
    router, service = _router()

    answer = router.answer(
        "Which five warehouses had the lowest case fill rate last month in West customer region?"
    )

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.FILL_RATE
    assert answer.intent.dimensions == (DimensionKey.WAREHOUSE,)
    assert answer.intent.quantity_basis == QuantityBasis.CASE_EQUIVALENTS
    assert answer.intent.limit == 5
    assert answer.intent.period.start == date(2026, 6, 1)
    assert answer.intent.period.end == date(2026, 6, 30)
    assert answer.intent.filters.customer_regions == ("West",)
    assert "2026-06-01 to 2026-06-30" in answer.interpretation
    assert answer.evidence[0].rows[0][0] == "WH01"
    assert any(call[0] == "service_by_dimension" for call in service.calls)


def test_illustrative_lowest_outlets_question_routes_to_outlet_breakdown() -> None:
    router, service = _router()
    original = service.service_by_dimension

    def outlet_aware(*args: object, **kwargs: object) -> pd.DataFrame:
        if len(args) > 1 and args[1] == "outlet":
            return pd.DataFrame(
                {
                    "dimension_value": ["OUT00001"],
                    "orders": [10],
                    "ordered_quantity": [100.0],
                    "delivered_quantity": [80.0],
                    "short_quantity": [20.0],
                    "fill_rate": [0.8],
                    "on_time_rate": [0.4],
                    "strict_otif_rate": [0.0],
                    "late_over_2h_rate": [0.2],
                }
            )
        return original(*args, **kwargs)  # type: ignore[arg-type]

    service.service_by_dimension = outlet_aware  # type: ignore[method-assign]
    answer = router.answer("Which five outlets had the lowest case fill rate last month?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.dimensions == (DimensionKey.OUTLET,)
    assert answer.intent.limit == 5
    assert answer.intent.quantity_basis == QuantityBasis.CASE_EQUIVALENTS
    assert answer.evidence[0].rows[0][0] == "OUT00001"


def test_why_fill_rate_question_compares_periods_and_returns_measured_contributors() -> None:
    router, service = _router()

    answer = router.answer("Why did fill rate drop in West customer region in FY 2026-27 Q1?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None and answer.intent.explain_change
    assert answer.intent.filters.customer_regions == ("West",)
    assert len(answer.evidence) == 4
    assert answer.evidence[0].title == "Fill-rate comparison"
    assert answer.evidence[1].rows[0][0] == "SR01"
    assert "association" in answer.warnings[0]
    assert len([call for call in service.calls if call[0] == "shortage_contributors"]) == 6


def test_otif_by_customer_region_uses_latest_complete_fiscal_quarter() -> None:
    router, _ = _router()

    answer = router.ask("What was OTIF by customer region for the last complete quarter?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.STRICT_OTIF
    assert answer.intent.dimensions == (DimensionKey.CUSTOMER_REGION,)
    assert answer.intent.period.label == "FY 2026-27 Q1"
    assert answer.intent.period.start == date(2026, 4, 1)
    assert answer.intent.period.end == date(2026, 6, 30)
    assert "0%" in answer.warnings[0]
    assert answer.summary == "Strict OTIF is 0.0% for every customer region shown."
    assert "strict_otif_rate" in answer.evidence[0].columns


def test_return_question_rejects_multiple_groupings_without_running_a_query() -> None:
    router, service = _router()

    answer = router.answer(
        "Which categories drive the largest value of returns, and what is the leading "
        "reason code in Q1?"
    )

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert "one grouping dimension" in answer.summary
    assert not any(call[0] == "returns_by_dimension" for call in service.calls)


def test_chilled_excursions_by_month_calls_summary_for_each_q1_month() -> None:
    router, service = _router()

    answer = router.answer(
        "Temperature excursions per hundred chilled deliveries by month in FY 2026-27 Q1"
    )

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.dimensions == (DimensionKey.MONTH,)
    assert [row[0] for row in answer.evidence[0].rows] == [
        "April 2026",
        "May 2026",
        "June 2026",
    ]
    assert "May 2026" in answer.summary
    summary_calls = [call for call in service.calls if call[0] == "executive_summary"]
    assert len(summary_calls) == 3
    assert not any(call[0] == "cold_chain_by_dimension" for call in service.calls)


def test_ranked_chilled_dimension_uses_governed_volume_floor_and_warning() -> None:
    router, service = _router()

    answer = router.answer("Which warehouse has the highest cold-chain excursion rate in Q1?")

    assert answer.status == AnswerStatus.OK
    calls = [call for call in service.calls if call[0] == "cold_chain_by_dimension"]
    assert len(calls) == 1
    assert calls[0][1][2] == 25
    assert "100 chilled deliveries" in answer.summary
    assert any("fewer than 25" in warning for warning in answer.warnings)
    assert any("formula is unchanged" in warning for warning in answer.warnings)


def test_late_route_threshold_is_strictly_more_than_ten_percent() -> None:
    router, _ = _router()

    answer = router.answer(
        "Which routes are more than two hours late on more than one delivery in ten in Q1?"
    )

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.LATE_ROUTES
    assert answer.intent.late_threshold_minutes == 120
    assert answer.intent.rate_threshold == 0.10
    assert len(answer.evidence[0].rows) == 1
    assert answer.evidence[0].rows[0][0] == "RT0002"
    assert "at least 25 actual-date deliveries" in answer.summary
    assert "RT0003" not in str(answer.evidence[0].rows)


def test_market_price_question_is_typed_but_cleanly_requires_integration() -> None:
    router, service = _router()

    answer = router.answer(
        "For the top 20 SKUs by value, compare MRP with the lowest competitor price "
        "in Mumbai for Q1"
    )

    assert answer.status == AnswerStatus.INTEGRATION_REQUIRED
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.MARKET_PRICE_GAP
    assert answer.intent.dimensions == (DimensionKey.SKU,)
    assert answer.intent.limit == 20
    assert answer.intent.filters.cities == ("Mumbai",)
    assert not any(call[0].startswith("market") for call in service.calls)
    assert "No estimate was fabricated" in answer.warnings[0]


def test_freight_question_is_typed_but_cleanly_requires_integration() -> None:
    router, _ = _router()

    answer = router.answer("Freight cost per delivered case by warehouse last quarter")

    assert answer.status == AnswerStatus.INTEGRATION_REQUIRED
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.FREIGHT_PER_CASE
    assert answer.intent.dimensions == (DimensionKey.WAREHOUSE,)
    assert "partner freight invoices" in answer.sources


def test_optional_external_service_answers_price_and_freight_when_available() -> None:
    service = FakeMetricService()
    router = QuestionRouter(service, FakeExternalMetricService())

    price = router.answer("Compare top 20 SKU MRP with competitor prices in Mumbai in Q1")
    freight = router.answer("Freight cost per delivered case by warehouse in Q1")

    assert price.status == AnswerStatus.OK
    assert "1 of 2" in price.summary
    assert price.evidence[0].rows[0][0] == "SKU-1"
    assert freight.status == AnswerStatus.OK
    assert "WH02" in freight.summary
    assert "settled/paid" in freight.summary
    assert "independently" in freight.warnings[0]


def test_optional_external_service_answers_freight_at_route_grain() -> None:
    service = FakeMetricService()
    router = QuestionRouter(service, FakeExternalMetricService())

    answer = router.answer("Freight cost per delivered case by route in Q1")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.dimensions == (DimensionKey.ROUTE,)
    assert "RT0002" in answer.summary
    assert "service-period × route" in answer.warnings[0]


def test_freight_per_case_rejects_unsupported_carrier_attribution() -> None:
    service = FakeMetricService()
    router = QuestionRouter(service, FakeExternalMetricService())

    answer = router.answer("Freight cost per delivered case by carrier in Q1")

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert answer.intent is None
    assert "does not support dimension(s): carrier" in answer.summary
    assert answer.evidence == ()


def test_discontinued_sku_question_returns_row_level_evidence() -> None:
    router, service = _router()

    answer = router.answer(
        "Which outlets ordered a discontinued SKU after its discontinuation date in Q1?"
    )

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.DISCONTINUED_SKUS
    assert answer.intent.dimensions == (DimensionKey.OUTLET, DimensionKey.SKU)
    assert answer.evidence[0].rows[0][0] == "SO-1"
    assert any(call[0] == "discontinued_order_evidence" for call in service.calls)


def test_multiple_metrics_are_ambiguous_and_execute_nothing() -> None:
    router, service = _router()

    parsed = router.parse("Compare fill rate and OTIF by region last quarter")
    answer = router.answer("Compare fill rate and OTIF by region last quarter")

    assert parsed.status == ParseStatus.AMBIGUOUS
    assert parsed.candidates == (MetricKey.FILL_RATE, MetricKey.STRICT_OTIF)
    assert answer.status == AnswerStatus.AMBIGUOUS
    assert answer.interpretation == "No metric query was executed."
    assert service.calls == []


def test_unknown_question_and_unknown_filter_fail_transparently() -> None:
    router, _ = _router()

    unsupported = router.answer("How happy are our customers?")
    ambiguous = router.answer("What was fill rate in Atlantis region last month?")

    assert unsupported.status == AnswerStatus.UNSUPPORTED
    assert ambiguous.status == AnswerStatus.AMBIGUOUS
    assert "Unknown region" in ambiguous.summary


def test_unqualified_region_name_requires_customer_or_dc_clarification() -> None:
    router, service = _router()

    answer = router.answer("What was fill rate in West region last month?")

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert "customer region or DC/warehouse region" in answer.summary
    assert answer.interpretation == "No metric query was executed."
    analytical_calls = [
        call for call in service.calls if call[0] not in {"available_date_range", "filter_options"}
    ]
    assert analytical_calls == []


def test_outlet_ranking_paraphrase_preserves_dimension_limit_and_basis() -> None:
    router, _ = _router()

    parsed = router.parse("Show me the five worst stores by case fill rate last month")

    assert parsed.status == ParseStatus.READY
    assert parsed.intent is not None
    assert parsed.intent.dimensions == (DimensionKey.OUTLET,)
    assert parsed.intent.ranking == Ranking.WORST
    assert parsed.intent.limit == 5
    assert parsed.intent.quantity_basis == QuantityBasis.CASE_EQUIVALENTS


def test_explicit_dc_region_resolves_to_warehouse_geography() -> None:
    router, _ = _router()

    answer = router.answer("What was fill rate in West DC region last month?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.filters.customer_regions == ()
    assert answer.intent.filters.warehouse_regions == ("West",)


def test_out_of_range_explicit_period_is_not_silently_clamped() -> None:
    router, _ = _router()

    answer = router.answer("Fill rate from 2026-06-01 to 2026-07-31")

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert "partly available" in answer.summary


def test_sql_shaped_text_is_never_forwarded_or_executed() -> None:
    router, service = _router()
    hostile_text = "Fill rate by warehouse last month; DROP TABLE fct_order_service"

    answer = router.answer(hostile_text)

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.raw_question == hostile_text
    analytical_calls = [
        call for call in service.calls if call[0] not in {"available_date_range", "filter_options"}
    ]
    assert [call[0] for call in analytical_calls] == ["service_by_dimension"]


def test_ui_adapter_inherits_selected_scope_and_returns_transparent_payload() -> None:
    service = FakeMetricService()
    selected = FilterSet(
        date(2026, 5, 1),
        date(2026, 5, 31),
        customer_regions=("West",),
        promotion_codes=("PRM0001",),
        order_sources=("SFA_MOBILE",),
    )

    payload = answer_question(
        question="What was case fill rate by warehouse?",
        service=service,
        filters=selected,
        basis=QuantityBasis.EACHES,
        definitions={},
    )

    assert "WH01" in str(payload["answer"])
    metadata = payload["metadata"]
    assert isinstance(metadata, dict)
    assert "Selected dashboard period (2026-05-01 to 2026-05-31)" in str(metadata["interpretation"])
    assert "quantity basis=case_equivalents" in str(metadata["interpretation"])
    assert "promotion=PRM0001" in str(metadata["interpretation"])
    assert "order source=SFA_MOBILE" in str(metadata["interpretation"])
    assert isinstance(payload["evidence"], list)
    assert payload["evidence"][0]["source"] == "fct_order_service"  # type: ignore[index]


def test_specific_v2_metric_intents_take_precedence_over_broader_terms() -> None:
    router, _ = _router()
    examples = {
        "Show post-allocation fulfilment and allocation rate in Q1": (
            MetricKey.POST_ALLOCATION_FULFILMENT,
        ),
        "Show on-time in-full and on-time performance in Q1": (MetricKey.STRICT_OTIF,),
        "Show credit-note leakage and credit notes in Q1": (MetricKey.CREDIT_NOTE_LEAKAGE,),
        "Show competitor match coverage and competitor results": (MetricKey.COMPETITOR_COVERAGE,),
    }

    for question, expected in examples.items():
        parsed = router.parse(question)

        assert parsed.status == ParseStatus.READY
        assert parsed.intent is not None
        assert (parsed.intent.metric,) == expected


def test_allocation_and_post_allocation_metrics_use_distinct_governed_ratios() -> None:
    router, service = _router()

    allocation = router.answer("Which warehouses had the lowest allocation rate in Q1?")
    post_allocation = router.answer(
        "Which warehouse had the highest post-allocation fulfilment in Q1?"
    )

    assert allocation.status == AnswerStatus.OK
    assert allocation.intent is not None
    assert allocation.intent.metric == MetricKey.ALLOCATION_RATE
    assert allocation.evidence[0].rows[0][0] == "WH01"
    assert (
        allocation.evidence[0].rows[0][allocation.evidence[0].columns.index("allocation_rate")]
        == 0.82
    )
    assert post_allocation.status == AnswerStatus.OK
    assert post_allocation.intent is not None
    assert post_allocation.intent.metric == MetricKey.POST_ALLOCATION_FULFILMENT
    assert post_allocation.evidence[0].rows[0][0] == "WH01"
    assert {call[0] for call in service.calls} >= {
        "available_date_range",
        "service_by_dimension",
    }


def test_requested_and_actual_delivery_on_time_rates_keep_cohorts_separate() -> None:
    router, service = _router()

    requested = router.answer("Which routes had the lowest on-time rate in Q1?")
    actual = router.answer("Which routes had the lowest actual-delivery-date on-time rate in Q1?")
    pod = router.answer("Which routes had the lowest POD coverage in Q1?")

    assert requested.status == AnswerStatus.OK
    assert requested.intent is not None
    assert requested.intent.metric == MetricKey.ON_TIME_RATE
    assert requested.evidence[0].rows[0][0] == "RT0002"
    assert "requested-delivery" in requested.definition.casefold()
    assert actual.status == AnswerStatus.OK
    assert actual.intent is not None
    assert actual.intent.metric == MetricKey.DELIVERY_ON_TIME_RATE
    assert actual.evidence[0].rows[0][0] == "RT0002"
    assert "actual-delivery-date cohort" in actual.warnings[0]
    assert "below 25 deliveries" in actual.warnings[0]
    assert pod.status == AnswerStatus.OK
    assert pod.intent is not None and pod.intent.metric == MetricKey.POD_COVERAGE
    assert pod.evidence[0].rows[0][0] == "RT0002"
    exception_calls = [
        call for call in service.calls if call[0] == "delivery_exceptions_by_dimension"
    ]
    assert all(call[1][2] == 25 for call in exception_calls)  # type: ignore[index]


def test_delivery_failure_answer_uses_recorded_labels_without_causal_claim() -> None:
    router, service = _router()

    answer = router.answer("What are the leading delivery failure reasons in Q1?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.DELIVERY_FAILURES
    assert answer.intent.dimensions == (DimensionKey.FAILURE_REASON,)
    assert answer.evidence[0].rows[0] == (0.06, 6.0, 100.0)
    assert answer.evidence[1].rows[0][0] == "DF01"
    assert "do not establish root cause" in answer.warnings[0]
    assert any(call[0] == "failure_reason_pareto" for call in service.calls)


def test_backlog_breakdown_is_current_source_state_and_preserves_case_basis() -> None:
    router, service = _router()

    answer = router.answer(
        "Which two warehouses have the largest case backlog of overdue open orders in Q1?"
    )

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.BACKLOG
    assert answer.intent.quantity_basis == QuantityBasis.CASE_EQUIVALENTS
    assert answer.intent.limit == 2
    assert answer.evidence[0].rows[0][0] == "WH02"
    assert "current source order status" in answer.warnings[0]
    call = next(call for call in service.calls if call[0] == "backlog_by_dimension")
    assert call[1][2] == QuantityBasis.CASE_EQUIVALENTS  # type: ignore[index]


def test_backlog_rejects_dimensions_outside_the_governed_allowlist() -> None:
    router, service = _router()

    answer = router.answer("Show backlog by category in Q1")

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert "does not support dimension" in answer.summary
    assert not any(call[0] == "backlog_by_dimension" for call in service.calls)


def test_worst_short_delivery_exposure_keeps_largest_exposure_first() -> None:
    router, _ = _router()

    answer = router.answer("Which warehouse has the worst short-delivery value exposure in Q1?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.SHORT_DELIVERY_EXPOSURE
    assert answer.evidence[0].rows[0][0] == "WH02"
    assert "not accounting loss" in answer.warnings[0]


def test_worst_inventory_risk_keeps_largest_near_expiry_exposure_first() -> None:
    router, _ = _router()

    answer = router.answer("Which warehouse has the worst inventory risk in Q1?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.INVENTORY_RISK
    assert answer.evidence[0].rows[0][0] == "WH02"
    assert "latest weekly snapshot" in answer.warnings[0]


def test_inventory_rejects_filters_that_snapshot_cannot_apply() -> None:
    router, service = _router()

    answer = router.answer("Show inventory risk by warehouse in West customer region in Q1")

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert "cannot apply active filter(s): customer regions" in answer.summary
    assert "no query was run" in answer.summary
    assert not any(call[0] == "inventory_risk" for call in service.calls)


def test_credit_note_leakage_keeps_overall_rate_and_grouped_value_separate() -> None:
    router, service = _router()

    answer = router.answer("Show credit-note leakage by disposition in Q1")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.CREDIT_NOTE_LEAKAGE
    assert answer.intent.dimensions == (DimensionKey.DISPOSITION,)
    assert [block.title for block in answer.evidence] == [
        "Approved credit-note leakage rate",
        "Approved credit-note value by disposition",
        "Credit-note workflow status",
    ]
    assert "Grouped evidence is value, not a grouped rate" in answer.warnings[0]
    assert "accounting profit" in answer.warnings[0]
    assert any(call[0] == "return_disposition_summary" for call in service.calls)


def test_competitor_coverage_precedence_and_latest_source_warning() -> None:
    service = FakeMetricService()
    router = QuestionRouter(service, FakeExternalMetricService())

    answer = router.answer("What is competitor match coverage in Mumbai in Q1?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.COMPETITOR_COVERAGE
    assert answer.summary == (
        "Governed competitor-match coverage in Mumbai is 80.0% (80 of 100 current listings)."
    )
    assert "latest entity-resolution measure" in answer.warnings[0]
    assert "reporting-period filters do not apply" in answer.warnings[0]
    assert "not sales coverage" in answer.warnings[0]


def test_context_questions_fail_closed_without_publication_gated_service() -> None:
    router, _ = _router()

    weather = router.answer("How did rainy weather affect delivery service in Q1?")
    holiday = router.answer("How did delivery service compare on holidays in Q1?")

    for answer, metric in (
        (weather, MetricKey.WEATHER_ASSOCIATION),
        (holiday, MetricKey.HOLIDAY_ASSOCIATION),
    ):
        assert answer.status == AnswerStatus.INTEGRATION_REQUIRED
        assert answer.intent is not None and answer.intent.metric == metric
        assert "No association or estimate was fabricated" in answer.warnings[0]


def test_business_spelling_is_corrected_with_visible_provenance() -> None:
    router, _ = _router()

    answer = router.answer("Which warehose had the worst fil rte last month?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.FILL_RATE
    assert answer.intent.dimensions == (DimensionKey.WAREHOUSE,)
    assert answer.intent.spelling_corrections == (
        "warehose→warehouse",
        "fil→fill",
        "rte→rate",
    )
    assert "resolver=rules+spelling" in answer.interpretation
    assert "spelling=warehose→warehouse" in answer.interpretation

    outlet_question = router.parse("Which five outltes had the lowest case fill rte?")
    assert outlet_question.status == ParseStatus.READY
    assert outlet_question.intent is not None
    assert outlet_question.intent.dimensions == (DimensionKey.OUTLET,)
    assert "outltes→outlets" in outlet_question.intent.spelling_corrections

    unsupported = router.parse("Can you tell whether customers are satisfied?")
    assert unsupported.status == ParseStatus.UNSUPPORTED


def test_unknown_entity_code_is_not_autocorrected_but_gets_a_suggestion() -> None:
    router, service = _router()

    answer = router.answer("What was fill rate for WH03 last month?")

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert "Unknown filter code(s): WH03" in answer.summary
    assert "Did you mean" in answer.summary
    assert not any(call[0] == "executive_summary" for call in service.calls)


def test_ui_adapter_uses_session_memory_for_safe_follow_up_filters() -> None:
    service = FakeMetricService()
    memory = ConversationMemory()
    selected = FilterSet(date(2026, 4, 1), date(2026, 6, 30))

    first = answer_question(
        question="Which warehouses had the lowest fill rate?",
        service=service,
        filters=selected,
        basis=QuantityBasis.EACHES,
        definitions={},
        conversation_memory=memory,
    )
    follow_up = answer_question(
        question="What about East customer region?",
        service=service,
        filters=selected,
        basis=QuantityBasis.EACHES,
        definitions={},
        conversation_memory=memory,
    )

    assert memory.last_intent is not None
    assert memory.last_intent.filters.customer_regions == ("East",)
    assert "WH01" in str(first["answer"])
    metadata = follow_up["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["resolver"] == "conversation"
    assert metadata["resolver_provenance"] == "session-scoped typed intent memory"
    assert "metric" in metadata["inherited_fields"]
    assert "customer region=East" in str(metadata["interpretation"])


def test_ambiguous_follow_up_does_not_execute_or_corrupt_memory() -> None:
    service = FakeMetricService()
    memory = ConversationMemory()
    selected = FilterSet(date(2026, 4, 1), date(2026, 6, 30))
    answer_question(
        question="What was fill rate by warehouse?",
        service=service,
        filters=selected,
        basis=QuantityBasis.EACHES,
        definitions={},
        conversation_memory=memory,
    )
    original = memory.last_intent
    analytical_before = len(
        [call for call in service.calls if call[0] == "service_by_dimension"]
    )

    follow_up = answer_question(
        question="What about West?",
        service=service,
        filters=selected,
        basis=QuantityBasis.EACHES,
        definitions={},
        conversation_memory=memory,
    )

    assert "customer region or DC/warehouse region" in str(follow_up["answer"])
    assert memory.last_intent == original
    assert len([call for call in service.calls if call[0] == "service_by_dimension"]) == (
        analytical_before
    )


def test_semantic_fallback_can_only_select_an_allowlisted_metric() -> None:
    service = FakeMetricService()
    semantic = FakeSemanticResolver(
        _semantic_resolution(SemanticStatus.READY, "allocation_rate")
    )
    router = QuestionRouter(service, semantic_resolver=semantic)  # type: ignore[arg-type]

    answer = router.answer("Where is customer stock assignment weakest?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.metric == MetricKey.ALLOCATION_RATE
    assert answer.intent.resolver == "local_semantic"
    assert answer.intent.resolver_provenance == "fake@test (python)"
    assert answer.intent.resolver_confidence == 0.82
    assert answer.intent.matched_example == "How much stock was assigned?"
    assert semantic.questions == ["where is customer stock assignment weakest?"]
    assert any(call[0] == "executive_summary" for call in service.calls)


def test_semantic_ambiguity_fails_before_metric_execution() -> None:
    service = FakeMetricService()
    semantic = FakeSemanticResolver(
        _semantic_resolution(
            SemanticStatus.AMBIGUOUS,
            None,
            message="The wording is equally close to fill and allocation.",
        )
    )
    router = QuestionRouter(service, semantic_resolver=semantic)  # type: ignore[arg-type]

    answer = router.answer("Where is supply performance weakest?")

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert answer.intent is None
    assert answer.summary == "The wording is equally close to fill and allocation."
    analytical = [
        call for call in service.calls if call[0] not in {"available_date_range", "filter_options"}
    ]
    assert analytical == []


def test_exact_rules_keep_precedence_over_semantic_fallback() -> None:
    service = FakeMetricService()
    semantic = FakeSemanticResolver(
        _semantic_resolution(SemanticStatus.READY, "inventory_risk")
    )
    router = QuestionRouter(service, semantic_resolver=semantic)  # type: ignore[arg-type]

    answer = router.answer("What was fill rate last month?")

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None and answer.intent.metric == MetricKey.FILL_RATE
    assert answer.intent.resolver == "rules"
    assert semantic.questions == []


def test_unsupported_financial_and_forecast_requests_are_never_nearest_intent_guesses() -> None:
    service = FakeMetricService()
    semantic = FakeSemanticResolver(
        _semantic_resolution(SemanticStatus.READY, "market_price_gap")
    )
    router = QuestionRouter(service, semantic_resolver=semantic)  # type: ignore[arg-type]

    profit = router.answer("What is our profit?")
    forecast = router.answer("Forecast fill rate next month")

    assert profit.status == AnswerStatus.UNSUPPORTED
    assert "does not support accounting profit" in profit.summary
    assert forecast.status == AnswerStatus.UNSUPPORTED
    assert "does not forecast" in forecast.summary
    assert semantic.questions == []
    assert not any(call[0] == "executive_summary" for call in service.calls)


@pytest.mark.parametrize(
    "question",
    [
        "Now forecast fill rate next month",
        "What about profit and fill rate?",
        "What will OTIF be next quarter?",
        "OTIF tomorrow",
        "How much inventory should we order?",
        "Which route should we use tomorrow?",
        "Who is responsible for delivery failures?",
        "Should we discontinue this SKU?",
        "Forcast fill rate",
        "Predcit OTIF",
        "Show financial lossses from delivery failures",
    ],
)
def test_future_financial_prescriptive_and_attribution_questions_fail_closed(
    question: str,
) -> None:
    router, service = _router()

    answer = router.answer(question)

    assert answer.status == AnswerStatus.UNSUPPORTED
    assert answer.interpretation == "No metric query was executed."
    assert service.calls == []


@pytest.mark.parametrize(
    ("question", "unsupported_filter"),
    [
        ("Fill rate in Mumbai", "cities"),
        ("Backlog in Mumbai", "cities"),
        ("Weather service in Mumbai", "cities"),
        ("Freight per case in Mumbai", "cities"),
    ],
)
def test_metric_specific_filter_scope_rejects_silently_ignored_filters(
    question: str,
    unsupported_filter: str,
) -> None:
    router, service = _router()

    answer = router.answer(question)

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert unsupported_filter in answer.summary
    assert "no query was run" in answer.summary
    analytical = [
        call for call in service.calls if call[0] not in {"available_date_range", "filter_options"}
    ]
    assert analytical == []


def test_competitor_coverage_rejects_operational_region_filter() -> None:
    service = FakeMetricService()
    router = QuestionRouter(service, FakeExternalMetricService())

    answer = router.answer("Competitor match coverage in West customer region")

    assert answer.status == AnswerStatus.AMBIGUOUS
    assert "customer regions" in answer.summary


def test_multiple_dimensions_and_promotion_mechanic_are_not_silently_truncated() -> None:
    router, service = _router()

    multiple = router.answer("Fill rate by warehouse and route")
    mechanic = router.parse("Fill rate by promotion mechanic")

    assert multiple.status == AnswerStatus.AMBIGUOUS
    assert "one grouping dimension" in multiple.summary
    assert mechanic.status == ParseStatus.READY
    assert mechanic.intent is not None
    assert mechanic.intent.dimensions == (DimensionKey.PROMOTION_MECHANIC,)
    assert not any(call[0] == "service_by_dimension" for call in service.calls)


def test_valid_words_are_never_fuzzy_rewritten_into_a_metric() -> None:
    normalized, corrections = normalize_business_spelling("Which router is late?")
    router, service = _router()

    answer = router.answer("Which router is late?")

    assert normalized == "which router is late?"
    assert corrections == ()
    assert answer.status == AnswerStatus.UNSUPPORTED
    assert service.calls == []


def test_adverse_metric_rankings_honor_explicit_lowest_direction() -> None:
    router, _ = _router()
    service = FakeMetricService()
    external_router = QuestionRouter(service, FakeExternalMetricService())

    backlog = router.answer("Which warehouse has the lowest backlog?")
    failures = router.answer("Lowest delivery failure rate by route")
    returns = router.answer("Lowest returns by category")
    chilled = router.answer("Lowest cold-chain excursion rate by warehouse")
    freight = external_router.answer("Lowest freight per case by warehouse")

    assert backlog.evidence[0].rows[0][0] == "WH01"
    assert failures.evidence[1].rows[0][0] == "RT0001"
    assert returns.evidence[0].rows[0][0] == "Snacks"
    assert chilled.evidence[0].rows[0][0] == "WH02"
    assert freight.evidence[0].rows[0][0] == "WH01"
    for answer in (backlog, failures, returns, chilled, freight):
        assert answer.intent is not None and answer.intent.ranking == Ranking.BEST
        assert "ranking=best" in answer.interpretation


def test_generic_on_time_delivery_uses_requested_cohort_unless_actual_date_is_explicit() -> None:
    router, _ = _router()

    generic = router.parse("Show on-time delivery by warehouse")
    actual = router.parse("Show on-time delivery by warehouse using the actual delivery cohort")

    assert generic.intent is not None and generic.intent.metric == MetricKey.ON_TIME_RATE
    assert actual.intent is not None
    assert actual.intent.metric == MetricKey.DELIVERY_ON_TIME_RATE


def test_monthly_fill_rate_ranking_honors_best_and_worst() -> None:
    router, _ = _router()

    best = router.answer("Which month had the best fill rate in all available history?")
    worst = router.answer("Which month had the worst fill rate in all available history?")

    assert best.status == AnswerStatus.OK
    assert worst.status == AnswerStatus.OK
    assert best.evidence[0].rows[0][0] == date(2026, 5, 1)
    assert worst.evidence[0].rows[0][0] == date(2026, 4, 1)


def test_metric_specific_date_ranges_expose_actual_delivery_and_return_dates() -> None:
    router, service = _router()

    delivery = router.parse("Delivery failure rate on 2026-07-04")
    returns = router.parse("Returns from 2026-07-20 to 2026-07-29")

    assert delivery.status == ParseStatus.READY
    assert returns.status == ParseStatus.READY
    bases = [value for name, value in service.calls if name == "available_date_range"]
    assert "actual_delivery" in bases
    assert "returns" in bases


def test_market_price_category_filter_is_applied_but_category_grouping_is_rejected() -> None:
    service = FakeMetricService()
    router = QuestionRouter(service, FakeExternalMetricService())

    filtered = router.answer("Competitor price gap for Dairy in Mumbai")
    grouped = router.answer("Competitor price gap by category in Mumbai")

    assert filtered.status == AnswerStatus.OK
    assert filtered.intent is not None
    assert filtered.intent.filters.categories == ("Dairy",)
    assert grouped.status == AnswerStatus.AMBIGUOUS
    assert "does not support dimension" in grouped.summary


def test_top_skus_by_value_is_scope_not_price_gap_ranking() -> None:
    router, _ = _router()

    parsed = router.parse(
        "For the top 20 SKUs by value, compare MRP with the lowest competitor price in Mumbai"
    )

    assert parsed.intent is not None
    assert parsed.intent.limit == 20
    assert parsed.intent.ranking == Ranking.NONE


def test_rank_skus_by_price_gap_requests_largest_gap_ordering() -> None:
    router, _ = _router()

    parsed = router.parse("Rank SKUs by competitor price gap in all available history")

    assert parsed.intent is not None
    assert parsed.intent.metric == MetricKey.MARKET_PRICE_GAP
    assert parsed.intent.ranking == Ranking.WORST


def test_all_catalogue_examples_survive_rule_precedence_and_router_validation() -> None:
    resolver = CatalogSemanticResolver()
    catalog = load_intent_catalog()

    for definition in catalog.intents:
        for example in definition.examples:
            service = FakeMetricService()
            router = QuestionRouter(
                service,
                semantic_resolver=resolver,  # type: ignore[arg-type]
            )
            parsed = router.parse(example)
            assert parsed.status == ParseStatus.READY, (definition.key, example, parsed.message)
            assert parsed.intent is not None
            assert parsed.intent.metric.value == definition.key, (
                definition.key,
                parsed.intent.metric.value,
                example,
            )


@pytest.mark.parametrize(
    ("question", "dimension"),
    [
        ("Rank distribution centres by on-time in-full delivery.", DimensionKey.WAREHOUSE),
        ("Rank warehouses by approved credit notes.", DimensionKey.WAREHOUSE),
        ("Show warehouses with the largest overdue backlog.", DimensionKey.WAREHOUSE),
        ("Rank warehouses by booked-value service exposure.", DimensionKey.WAREHOUSE),
        ("Rank SKUs by cases approaching expiry.", DimensionKey.SKU),
        ("Rank customer regions by delivery failure incidents.", DimensionKey.CUSTOMER_REGION),
        (
            "Which depots keep falling short after stock has already been assigned?",
            DimensionKey.WAREHOUSE,
        ),
    ],
)
def test_catalogue_grouping_language_survives_semantic_metric_resolution(
    question: str,
    dimension: DimensionKey,
) -> None:
    service = FakeMetricService()
    router = QuestionRouter(
        service,
        semantic_resolver=CatalogSemanticResolver(),  # type: ignore[arg-type]
    )

    parsed = router.parse(question)

    assert parsed.status == ParseStatus.READY
    assert parsed.intent is not None
    assert parsed.intent.dimensions == (dimension,)


def test_unsafe_follow_up_does_not_execute_or_overwrite_session_memory() -> None:
    service = FakeMetricService()
    memory = ConversationMemory()
    selected = FilterSet(date(2026, 4, 1), date(2026, 6, 30))
    answer_question(
        question="Fill rate by warehouse",
        service=service,
        filters=selected,
        basis=QuantityBasis.EACHES,
        definitions={},
        conversation_memory=memory,
    )
    original = memory.last_intent
    analytical_before = len(
        [call for call in service.calls if call[0] == "service_by_dimension"]
    )

    result = answer_question(
        question="What about profit by warehouse?",
        service=service,
        filters=selected,
        basis=QuantityBasis.EACHES,
        definitions={},
        conversation_memory=memory,
    )

    assert result["metadata"]["status"] == "unsupported"  # type: ignore[index]
    assert "does not support accounting profit" in str(result["answer"])
    assert memory.last_intent == original
    assert len([call for call in service.calls if call[0] == "service_by_dimension"]) == (
        analytical_before
    )
