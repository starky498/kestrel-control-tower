from __future__ import annotations

from datetime import date

import pandas as pd

from kestrel.metrics.service import FilterSet, MetricValue, QuantityBasis
from kestrel.nlq import (
    AnswerStatus,
    DimensionKey,
    MetricKey,
    ParseStatus,
    QuestionRouter,
    answer_question,
)


class FakeMetricService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def available_date_range(self) -> tuple[date, date]:
        self.calls.append(("available_date_range", None))
        return date(2025, 1, 1), date(2026, 6, 30)

    def filter_options(self) -> dict[str, list[str]]:
        self.calls.append(("filter_options", None))
        return {
            "customer_regions": ["Central", "East", "North", "South", "West"],
            "warehouse_regions": ["Central", "East", "North", "South", "West"],
            "warehouse_codes": ["WH01", "WH02"],
            "route_codes": ["RT0001", "RT0002"],
            "outlet_codes": ["OUT00001", "OUT00002"],
            "channels": ["GT", "MT", "HORECA", "ECOM_DARKSTORE"],
        }

    def executive_summary(
        self, filters: FilterSet, basis: QuantityBasis = QuantityBasis.EACHES
    ) -> dict[str, MetricValue]:
        self.calls.append(("executive_summary", (filters, basis)))
        monthly_rate = {4: 4.0, 5: 6.0, 6: 2.0}.get(filters.start_date.month, 3.0)
        return {
            "fill_rate": MetricValue("fill_rate", 0.9, 900.0, 1000.0, "percent", 10),
            "strict_otif": MetricValue("strict_otif", 0.0, 0.0, 10.0, "percent", 10),
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

    def service_trend(
        self, filters: FilterSet, basis: QuantityBasis = QuantityBasis.EACHES
    ) -> pd.DataFrame:
        self.calls.append(("service_trend", (filters, basis)))
        return pd.DataFrame(
            {
                "month": [date(2026, 4, 1), date(2026, 5, 1)],
                "orders": [10, 20],
                "fill_rate": [0.8, 0.9],
                "on_time_rate": [0.4, 0.5],
                "strict_otif_rate": [0.0, 0.0],
                "short_quantity": [20.0, 10.0],
            }
        )

    def cold_chain_by_dimension(
        self, filters: FilterSet, dimension: str, *, limit: int = 20
    ) -> pd.DataFrame:
        self.calls.append(("cold_chain_by_dimension", (filters, dimension, limit)))
        return pd.DataFrame(
            {
                "dimension_value": ["WH01"],
                "chilled_deliveries": [100],
                "excursions": [5],
                "excursions_per_100": [5.0],
            }
        )

    def returns_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        statuses: tuple[str, ...] = ("APPROVED",),
        limit: int = 20,
    ) -> pd.DataFrame:
        self.calls.append(("returns_by_dimension", (filters, dimension, statuses, limit)))
        value = "Dairy" if dimension == "category" else "RT06"
        return pd.DataFrame(
            {
                "dimension_value": [value],
                "credit_note_lines": [5],
                "return_eaches": [50.0],
                "return_case_equivalents": [5.0],
                "credit_note_value_inr": [12500.0],
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
        self, filters: FilterSet, dimension: str, *, limit: int = 20
    ) -> pd.DataFrame:
        self.calls.append(("shortage_contributors", (filters, dimension, limit)))
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
                "freight_cost_per_delivered_case_inr": [40.0, 20.0],
            }
        )
        frame.attrs["attribution"] = "Aggregated independently at service-period × warehouse."
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


def _router() -> tuple[QuestionRouter, FakeMetricService]:
    service = FakeMetricService()
    return QuestionRouter(service), service


def test_fill_rate_by_warehouse_parses_filters_basis_and_relative_month() -> None:
    router, service = _router()

    answer = router.answer(
        "Which five warehouses had the lowest case fill rate last month in West region?"
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
    answer = router.answer(
        "Which five outlets had the lowest case fill rate last month?"
    )

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.dimensions == (DimensionKey.OUTLET,)
    assert answer.intent.limit == 5
    assert answer.intent.quantity_basis == QuantityBasis.CASE_EQUIVALENTS
    assert answer.evidence[0].rows[0][0] == "OUT00001"


def test_why_fill_rate_question_compares_periods_and_returns_measured_contributors() -> None:
    router, service = _router()

    answer = router.answer("Why did fill rate drop in West in FY 2026-27 Q1?")

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


def test_return_question_calls_category_and_reason_allowlisted_methods() -> None:
    router, service = _router()

    answer = router.answer(
        "Which categories drive the largest value of returns, and what is the leading "
        "reason code in Q1?"
    )

    assert answer.status == AnswerStatus.OK
    assert answer.intent is not None
    assert answer.intent.dimensions == (
        DimensionKey.CATEGORY,
        DimensionKey.RETURN_REASON,
    )
    assert len(answer.evidence) == 2
    return_calls = [call for call in service.calls if call[0] == "returns_by_dimension"]
    assert [call[1][1] for call in return_calls] == ["category", "reason"]  # type: ignore[index]
    assert all(call[1][2] == ("APPROVED",) for call in return_calls)  # type: ignore[index]


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
    assert "independently" in freight.warnings[0]


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
    assert isinstance(payload["evidence"], list)
    assert payload["evidence"][0]["source"] == "fct_order_service"  # type: ignore[index]
