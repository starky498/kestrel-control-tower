from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from kestrel.metrics.service import AnalyticsService, FilterSet, QuantityBasis


def _build_metric_fixture(path: Path) -> None:
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE fct_order_service AS
        SELECT * FROM (VALUES
            (1, DATE '2026-04-10', DATE '2026-04-11', 'West', 'West', 'WH01',
             'RT0001', 'OUT001', 'GT', TRUE, TRUE, 'DELIVERED',
             100.0, 95.0, 90.0, 90.0, 10.0, 10.0, 9.5, 9.0, 9.0, 1.0,
             TIMESTAMP '2026-04-11 10:00:00', TIMESTAMP '2026-04-11 09:30:00',
             TRUE, FALSE, FALSE, FALSE, 'SFA_MOBILE', 'PRM0001',
             'Spring promotion', 'PERCENT_OFF', TRUE),
            (2, DATE '2026-04-12', DATE '2026-04-13', 'West', 'West', 'WH02',
             'RT0002', 'OUT002', 'MT', TRUE, TRUE, 'PARTIAL',
             10.0, 10.0, 10.0, 10.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.0,
             TIMESTAMP '2026-04-13 10:00:00', TIMESTAMP '2026-04-13 13:00:00',
             FALSE, TRUE, TRUE, FALSE, 'PARTNER_API', 'NO_PROMOTION',
             'No promotion', 'No promotion', FALSE),
            (3, DATE '2026-04-01', DATE '2026-04-05', 'West', 'West', 'WH01',
             'RT0001', 'OUT001', 'GT', FALSE, TRUE, 'OPEN',
             50.0, 40.0, 0.0, 0.0, 50.0, 5.0, 4.0, 0.0, 0.0, 5.0,
             NULL, NULL,
             FALSE, FALSE, FALSE, FALSE, 'SFA_MOBILE', 'PRM0001',
             'Spring promotion', 'PERCENT_OFF', TRUE),
            (4, DATE '2026-03-10', DATE '2026-03-11', 'West', 'West', 'WH01',
             'RT0001', 'OUT001', 'GT', TRUE, TRUE, 'DELIVERED',
             100.0, 80.0, 70.0, 70.0, 30.0, 10.0, 8.0, 7.0, 7.0, 3.0,
             TIMESTAMP '2026-03-11 10:00:00', TIMESTAMP '2026-03-11 09:30:00',
             TRUE, FALSE, FALSE, FALSE, 'SFA_MOBILE', 'PRM0001',
             'Spring promotion', 'PERCENT_OFF', TRUE),
            (5, DATE '2026-03-12', DATE '2026-03-13', 'West', 'West', 'WH02',
             'RT0002', 'OUT002', 'MT', TRUE, TRUE, 'DELIVERED',
             10.0, 10.0, 10.0, 10.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.0,
             TIMESTAMP '2026-03-13 10:00:00', TIMESTAMP '2026-03-13 09:30:00',
             TRUE, TRUE, FALSE, TRUE, 'PARTNER_API', 'NO_PROMOTION',
             'No promotion', 'No promotion', FALSE)
        ) t(
            order_id, order_date, requested_delivery_date, customer_region_name,
            warehouse_region_name, warehouse_code, route_code, outlet_code, channel,
            is_eligible_service, is_eligible_service_outlet, order_status,
            ordered_eaches, allocated_eaches, delivered_eaches,
            capped_delivered_eaches, short_eaches,
            ordered_case_equivalents, allocated_case_equivalents,
            delivered_case_equivalents, capped_delivered_case_equivalents,
            short_case_equivalents,
            planned_arrival_ts, actual_arrival_ts,
            on_time_by_timestamp, strict_in_full, late_over_2h, strict_otif,
            source_system, promotion_code, promotion_name, promotion_mechanic,
            promotion_applied
        );

        CREATE TABLE fct_delivery AS
        SELECT * FROM (VALUES
            (1, 101, DATE '2026-04-11', 'West', 'West', 'WH01', 'RT0001', 'OUT001', 'GT',
             TRUE, TRUE, TRUE, 11.0, 'DN-1', 'Vendor A',
             TIMESTAMP '2026-04-11 10:00:00', TIMESTAMP '2026-04-11 09:30:00',
             -20, -30, TRUE, TRUE, FALSE, TRUE, FALSE, 'DELIVERED', NULL,
             'SFA_MOBILE', 'PRM0001'),
            (2, 102, DATE '2026-04-13', 'West', 'West', 'WH02', 'RT0002', 'OUT002', 'MT',
             TRUE, TRUE, FALSE, 7.0, 'DN-2', 'Vendor B',
             TIMESTAMP '2026-04-13 10:00:00', TIMESTAMP '2026-04-13 13:00:00',
             -5, 180, FALSE, TRUE, TRUE, FALSE, TRUE, 'FAILED', 'DF01',
             'PARTNER_API', 'NO_PROMOTION')
        ) t(
            delivery_id, order_id, delivery_date, customer_region_name, warehouse_region_name,
            warehouse_code, route_code, outlet_code, channel, is_eligible_service,
            has_chilled_product, temperature_excursion_flag, max_temp_celsius,
            delivery_note_number,
            telematics_vendor, planned_arrival_ts, actual_arrival_ts,
            stored_delay_minutes, derived_delay_minutes, on_time_by_timestamp,
            on_time_by_stored_delay, late_over_2h, pod_captured,
            delay_source_conflict, delivery_status,
            failure_reason_code, source_system, promotion_code
        );

        CREATE TABLE fct_order_line AS
        SELECT * FROM (VALUES
            (1001, 101, 'SO-101', DATE '2026-04-11', 'West', 'West', 'WH01',
             'RT0001', 'OUT001', 'GT', TRUE, 'SFA_MOBILE', 'PRM0001',
             '2026-04-10 08:00:00', TIMESTAMP '2026-04-10 08:00:00',
             'PARSED_SFA_MOBILE_IST',
             'Spring promotion', 'PERCENT_OFF', TRUE, 'SKU-1', 'Chilled Product',
             'Dairy', TRUE, 100.0, 95.0, 90.0, 10.0, 5.0, 5.0,
             10.0, 9.5, 9.0, 1.0, 0.5, 0.5, 1000.0, 900.0, 100.0,
             50.0, 50.0, 'SR01'),
            (1002, 102, 'SO-102', DATE '2026-04-13', 'West', 'West', 'WH02',
             'RT0002', 'OUT002', 'MT', TRUE, 'PARTNER_API', 'NO_PROMOTION',
             '2026-04-12T03:00:00Z', TIMESTAMP '2026-04-12 08:30:00',
             'PARSED_PARTNER_API_UTC_TO_IST',
             'No promotion', 'No promotion', FALSE, 'SKU-2', 'Frozen Product',
             'Frozen', TRUE, 10.0, 10.0, 10.0, 0.0, 0.0, 0.0,
             1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 100.0, 100.0, 0.0,
             0.0, 0.0, NULL)
        ) t(
            order_line_id, order_id, order_number, requested_delivery_date,
            customer_region_name,
            warehouse_region_name, warehouse_code, route_code, outlet_code, channel,
            is_eligible_service, source_system, promotion_code, created_at_raw,
            created_at_ist, created_at_parse_status, promotion_name,
            promotion_mechanic, promotion_applied, sku_code, product_name, category,
            is_chilled, ordered_eaches, allocated_eaches, delivered_eaches,
            short_eaches, allocation_short_eaches, post_allocation_short_eaches,
            ordered_case_equivalents, allocated_case_equivalents,
            delivered_case_equivalents, short_case_equivalents,
            allocation_short_case_equivalents,
            post_allocation_short_case_equivalents, line_value_inr,
            estimated_dispatch_value_inr, short_delivery_value_exposure_inr,
            allocation_short_value_exposure_inr,
            post_allocation_short_value_exposure_inr, short_reason_code
        );

        CREATE TABLE fct_return_credit_note AS
        SELECT * FROM (VALUES
            (1, DATE '2026-04-20', 'West', 'West', 'WH01', 'RT0001', 'OUT001', 'GT',
             'APPROVED', 50.0, 'CN-1', 101, 1001, 'Outlet One', 'SKU-1',
             'Chilled Product', 'EACH', -2.0, 2.0, TRUE, 2.0, 0.2,
             'RT06_COLD_CHAIN_BREACH', TRUE, 'SCRAP', 'SFA_MOBILE', 'PRM0001',
             'Spring promotion', 'PERCENT_OFF', TRUE)
        ) t(
            return_id, return_date, customer_region_name, warehouse_region_name,
            warehouse_code, route_code, outlet_code, channel, credit_note_status,
            credit_note_value_inr, credit_note_number, order_id, order_line_id,
            outlet_name, sku_code, product_name, qty_uom, return_qty_raw,
            return_qty_normalized, return_sign_was_negative, return_eaches,
            return_case_equivalents, return_reason_code, is_cold_chain_return,
            disposition, source_system, promotion_code, promotion_name,
            promotion_mechanic, promotion_applied
        );

        CREATE TABLE fct_inventory_snapshot AS
        SELECT * FROM (VALUES
            (1, DATE '2026-04-27', 'West', 'WH01', 'Warehouse One', 'SKU-1',
             'Chilled Product', 'Dairy', 'Milk', TRUE, 'CHILLED', 'B-1',
             40.0, 400.0, 10.0, 30.0, 6.0, DATE '2026-05-27', 30,
             '0-30', 0.0, 0.0, 5.0),
            (2, DATE '2026-04-27', 'West', 'WH02', 'Warehouse Two', 'SKU-2',
             'Ambient Product', 'Snacks', 'Crisps', FALSE, 'AMBIENT', 'B-2',
             25.0, 250.0, 5.0, 20.0, 10.0, DATE '2026-05-28', 31,
             '31-60', 0.0, 0.0, 24.0)
        ) t(
            snapshot_id, snapshot_date, warehouse_region_name, warehouse_code,
            warehouse_name, sku_code, product_name, category, subcategory,
            is_chilled, storage_temp_band, batch_id, on_hand_cases, on_hand_eaches,
            allocated_cases, available_cases, days_of_cover, expiry_date, expiry_days,
            ageing_bucket, damaged_cases, blocked_cases, storage_temp_celsius
        );
        """
    )
    connection.close()


@pytest.fixture()
def service(tmp_path: Path) -> AnalyticsService:
    database = tmp_path / "metrics.duckdb"
    _build_metric_fixture(database)
    return AnalyticsService(database)


def test_executive_summary_uses_weighted_ratio_of_sums(service: AnalyticsService) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))
    summary = service.executive_summary(filters, QuantityBasis.EACHES)

    assert summary["fill_rate"].value == pytest.approx(100 / 110)
    assert summary["allocation_rate"].value == pytest.approx(105 / 110)
    assert summary["post_allocation_fulfilment"].value == pytest.approx(100 / 105)
    assert summary["overdue_backlog_orders"].value == 1
    assert summary["strict_otif"].value == 0
    assert summary["on_time_rate"].value == pytest.approx(0.5)
    assert summary["temperature_excursions_per_100"].value == pytest.approx(50)
    assert summary["approved_credit_note_rate"].value == pytest.approx(0.05)
    assert summary["near_expiry_cases"].value == 30


def test_executive_timing_rates_exclude_missing_parsed_timestamps(
    service: AnalyticsService,
) -> None:
    with duckdb.connect(str(service.database_path)) as connection:
        connection.execute(
            """
            UPDATE fct_order_service
            SET planned_arrival_ts = NULL, actual_arrival_ts = NULL
            WHERE order_id = 2
            """
        )

    summary = service.executive_summary(
        FilterSet(date(2026, 4, 1), date(2026, 4, 30))
    )

    assert summary["on_time_rate"].value == 1.0
    assert summary["on_time_rate"].denominator == 1.0
    assert summary["late_over_2h_rate"].value == 0.0
    assert summary["late_over_2h_rate"].denominator == 1.0


def test_fill_rate_caps_delivery_at_ordered_quantity_per_line(
    service: AnalyticsService,
) -> None:
    with duckdb.connect(str(service.database_path)) as connection:
        connection.execute("ALTER TABLE fct_order_service ALTER delivered_eaches TYPE DOUBLE")
        connection.execute(
            "ALTER TABLE fct_order_service ALTER capped_delivered_eaches TYPE DOUBLE"
        )
        connection.execute(
            "ALTER TABLE fct_order_service "
            "ALTER delivered_case_equivalents TYPE DOUBLE"
        )
        connection.execute(
            "ALTER TABLE fct_order_service "
            "ALTER capped_delivered_case_equivalents TYPE DOUBLE"
        )
        connection.execute(
            """
            UPDATE fct_order_service
            SET delivered_eaches = CASE order_id WHEN 1 THEN 200 ELSE 0 END,
                capped_delivered_eaches = CASE order_id WHEN 1 THEN 100 ELSE 0 END,
                delivered_case_equivalents = CASE order_id WHEN 1 THEN 20 ELSE 0 END,
                capped_delivered_case_equivalents = CASE order_id WHEN 1 THEN 10 ELSE 0 END
            WHERE order_id IN (1, 2)
            """
        )

    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))
    summary = service.executive_summary(filters, QuantityBasis.EACHES)

    assert summary["fill_rate_eaches"].value == pytest.approx(100 / 110)
    assert summary["fill_rate_case_equivalents"].value == pytest.approx(10 / 11)
    assert summary["post_allocation_fulfilment"].value == pytest.approx(200 / 105)


def test_case_equivalent_basis_and_warehouse_filter(service: AnalyticsService) -> None:
    filters = FilterSet(
        date(2026, 4, 1),
        date(2026, 4, 30),
        warehouse_codes=("WH01",),
    )
    summary = service.executive_summary(filters, QuantityBasis.CASE_EQUIVALENTS)

    assert summary["fill_rate"].value == pytest.approx(0.9)
    assert summary["allocation_rate"].value == pytest.approx(0.95)
    assert summary["post_allocation_fulfilment"].value == pytest.approx(90 / 95)
    assert summary["near_expiry_cases"].value == 30


def test_executive_summary_always_exposes_both_fill_bases(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    summary = service.executive_summary(filters, QuantityBasis.CASE_EQUIVALENTS)

    assert summary["fill_rate"].value == pytest.approx(10 / 11)
    assert summary["fill_rate_eaches"].value == pytest.approx(100 / 110)
    assert summary["fill_rate_case_equivalents"].value == pytest.approx(10 / 11)
    assert summary["short_delivery_value_exposure_inr"].value == 100
    assert summary["short_delivery_value_exposure_inr"].records == 1


def test_recorded_promotion_and_order_source_are_filterable_and_analysable(
    service: AnalyticsService,
) -> None:
    options = service.filter_options()
    filters = FilterSet(
        date(2026, 4, 1),
        date(2026, 4, 30),
        promotion_codes=("PRM0001",),
        order_sources=("SFA_MOBILE",),
    )

    summary = service.executive_summary(filters)
    promotions = service.service_by_dimension(filters, "promotion", min_orders=1)
    sources = service.service_by_dimension(filters, "order_source", min_orders=1)

    assert options["promotion_codes"] == ["NO_PROMOTION", "PRM0001"]
    assert options["order_sources"] == ["PARTNER_API", "SFA_MOBILE"]
    assert summary["fill_rate"].value == pytest.approx(0.9)
    assert promotions["dimension_value"].tolist() == ["PRM0001"]
    assert sources["dimension_value"].tolist() == ["SFA_MOBILE"]


def test_fulfilment_flow_uses_line_cohort_and_preaggregated_returns(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    eaches = service.fulfilment_flow(filters, QuantityBasis.EACHES)
    cases = service.fulfilment_flow(filters, QuantityBasis.CASE_EQUIVALENTS)

    assert eaches.iloc[0].to_dict() == {
        "eligible_order_lines": 2,
        "ordered_quantity": 110.0,
        "allocated_quantity": 105.0,
        "delivered_quantity": 100.0,
        "returned_quantity": 2.0,
        "allocation_shortfall_quantity": 5.0,
        "post_allocation_shortfall_quantity": 5.0,
    }
    assert cases.iloc[0]["ordered_quantity"] == 11
    assert cases.iloc[0]["returned_quantity"] == pytest.approx(0.2)
    assert "requested delivery date" in eaches.attrs["date_basis"].lower()
    with pytest.raises(ValueError, match="At least one return status"):
        service.fulfilment_flow(filters, return_statuses=())


def test_service_line_evidence_keeps_native_source_and_promotion_fields(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    frame = service.service_line_evidence(filters)

    assert frame.iloc[0]["order_line_id"] == 1001
    assert frame.iloc[0]["source_system"] == "SFA_MOBILE"
    assert frame.iloc[0]["created_at_raw"] == "2026-04-10 08:00:00"
    assert frame.iloc[0]["created_at_ist"] == pd.Timestamp("2026-04-10 08:00:00")
    assert frame.iloc[0]["created_at_parse_status"] == "PARSED_SFA_MOBILE_IST"
    assert frame.iloc[0]["promotion_code"] == "PRM0001"
    assert frame.iloc[0]["returned_eaches"] == 2
    assert frame.iloc[0]["short_delivery_value_exposure_inr"] == 100
    assert frame.attrs["grain"] == "One eligible order line"


def test_service_dimension_is_allow_listed(service: AnalyticsService) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))
    with pytest.raises(ValueError, match="Unsupported service dimension"):
        service.service_by_dimension(filters, "arbitrary_sql")


def test_service_dimension_volume_floor_is_explicit(service: AnalyticsService) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    frame = service.service_by_dimension(filters, "warehouse", min_orders=2)

    assert frame.empty
    assert frame.attrs["min_orders"] == 2
    with pytest.raises(ValueError, match="min_orders must be positive"):
        service.service_by_dimension(filters, "warehouse", min_orders=0)


def test_service_rankings_apply_volume_and_comparable_period_rules(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    best = service.service_rankings(
        filters, "warehouse", ranking="best", min_orders=1
    )
    improved = service.service_rankings(
        filters, "warehouse", ranking="most_improved", min_orders=1
    )

    assert best.iloc[0]["dimension_value"] == "WH02"
    assert best.attrs["min_orders"] == 1
    assert improved.iloc[0]["dimension_value"] == "WH01"
    assert improved.iloc[0]["fill_rate_change_pp"] == pytest.approx(20)


def test_delivery_exception_metrics_keep_conflicts_and_evidence_visible(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    summary = service.delivery_exception_summary(filters)
    routes = service.delivery_exceptions_by_dimension(
        filters, "route", min_deliveries=1
    )
    pareto = service.failure_reason_pareto(filters)
    evidence = service.delivery_exception_evidence(filters)

    assert summary["delivery_on_time_rate"].value == pytest.approx(0.5)
    assert summary["delivery_late_over_2h_rate"].value == pytest.approx(0.5)
    assert summary["pod_coverage_rate"].value == pytest.approx(0.5)
    assert summary["delay_source_conflict_rate"].value == pytest.approx(0.5)
    assert summary["recorded_failure_rate"].value == pytest.approx(0.5)
    assert routes.iloc[0]["dimension_value"] == "RT0002"
    assert pareto.iloc[0]["failure_reason_code"] == "DF01"
    assert pareto.iloc[0]["cumulative_share"] == pytest.approx(1.0)
    assert evidence.iloc[0]["delivery_note_number"] == "DN-2"
    assert bool(evidence.iloc[0]["on_time_by_stored_delay"])
    assert not bool(evidence.iloc[0]["on_time_by_timestamp"])


def test_delivery_exception_dimension_and_threshold_are_governed(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    with pytest.raises(ValueError, match="Unsupported delivery exception dimension"):
        service.delivery_exceptions_by_dimension(filters, "driver_name")
    with pytest.raises(ValueError, match="min_deliveries must be positive"):
        service.delivery_exceptions_by_dimension(filters, "route", min_deliveries=0)


def test_inventory_discloses_filters_without_inventory_keys(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(
        date(2026, 4, 1),
        date(2026, 4, 30),
        customer_regions=("West",),
        route_codes=("RT0001",),
        outlet_codes=("OUT001",),
        channels=("GT",),
    )

    frame = service.inventory_risk(filters)

    assert frame.attrs["ignored_filters"] == (
        "customer region",
        "route",
        "outlet",
        "channel",
    )


def test_cold_chain_severity_evidence_supports_month_category_route_and_warehouse(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    route = service.cold_chain_by_dimension(filters, "route")
    category = service.cold_chain_by_dimension(filters, "category")
    warehouse = service.cold_chain_by_dimension(filters, "warehouse")
    trend = service.cold_chain_trend(filters)

    flagged_route = route.loc[route["dimension_value"] == "RT0001"].iloc[0]
    assert flagged_route["excursions"] == 1
    assert flagged_route["avg_excursion_max_temp_c"] == 11
    assert flagged_route["peak_excursion_max_temp_c"] == 11
    assert flagged_route["flagged_peak_8_to_12c"] == 1
    assert flagged_route["descriptive_peak_band"] == "Flagged peak >8-12C"
    assert set(category["dimension_value"]) == {"Dairy", "Frozen"}
    assert category.attrs["grain"] == "One delivery × chilled-category pair"
    assert not warehouse.empty
    assert trend["dimension_value"].tolist() == [pd.Timestamp("2026-04-01")]
    assert "not validated food-safety limits" in route.attrs["severity_definition"]


def test_inventory_batch_evidence_is_latest_as_of_and_discloses_filter_boundary(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(
        date(2026, 4, 1),
        date(2026, 4, 30),
        customer_regions=("West",),
        promotion_codes=("PRM0001",),
        order_sources=("SFA_MOBILE",),
    )

    frame = service.inventory_batch_evidence(filters)

    assert frame["batch_id"].tolist() == ["B-1"]
    assert bool(frame.iloc[0]["near_expiry_flag"])
    assert frame.attrs["snapshot_date"] == date(2026, 4, 27)
    assert frame.attrs["ignored_filters"] == (
        "customer region",
        "promotion",
        "order source",
    )


def test_short_delivery_exposure_is_line_value_share_not_profit_claim(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    by_reason = service.short_delivery_exposure(filters, "short_reason")
    by_category = service.short_delivery_exposure(filters, "category")

    assert by_reason.iloc[0]["dimension_value"] == "SR01"
    assert by_reason.iloc[0]["short_lines"] == 1
    assert by_reason.iloc[0]["short_quantity"] == 10
    assert by_reason.iloc[0]["short_delivery_value_exposure_inr"] == 100
    assert by_category.iloc[0]["dimension_value"] == "Dairy"
    assert "not accounting loss" in by_reason.attrs["value_definition"]


def test_return_disposition_keeps_restock_scrap_and_vendor_recovery_semantics(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    frame = service.return_disposition_summary(filters)

    assert frame["disposition"].tolist() == ["SCRAP"]
    assert frame.iloc[0]["approved_lines"] == 1
    assert frame.iloc[0]["approved_credit_note_value_inr"] == 50
    assert "not proof of cash receipt" in frame.attrs["warning"]


def test_cold_chain_returns_keep_exact_line_and_normalized_quantity_evidence(
    service: AnalyticsService,
) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))

    frame = service.cold_chain_return_evidence(filters)

    assert len(frame) == 1
    assert frame.iloc[0]["credit_note_number"] == "CN-1"
    assert frame.iloc[0]["order_line_id"] == 1001
    assert frame.iloc[0]["return_qty_raw"] == -2
    assert frame.iloc[0]["return_qty_normalized"] == 2
    assert bool(frame.iloc[0]["return_sign_was_negative"])
    assert frame.iloc[0]["credit_note_status"] == "APPROVED"
    assert "order_line_id" in frame.attrs["grain"]
