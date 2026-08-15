from datetime import date
from pathlib import Path

import duckdb
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
             100.0, 95.0, 90.0, 10.0, 10.0, 9.5, 9.0, 1.0,
             TRUE, FALSE, FALSE, FALSE),
            (2, DATE '2026-04-12', DATE '2026-04-13', 'West', 'West', 'WH02',
             'RT0002', 'OUT002', 'MT', TRUE, TRUE, 'PARTIAL',
             10.0, 10.0, 10.0, 0.0, 1.0, 1.0, 1.0, 0.0,
             FALSE, TRUE, TRUE, FALSE),
            (3, DATE '2026-04-01', DATE '2026-04-05', 'West', 'West', 'WH01',
             'RT0001', 'OUT001', 'GT', FALSE, TRUE, 'OPEN',
             50.0, 40.0, 0.0, 50.0, 5.0, 4.0, 0.0, 5.0,
             FALSE, FALSE, FALSE, FALSE),
            (4, DATE '2026-03-10', DATE '2026-03-11', 'West', 'West', 'WH01',
             'RT0001', 'OUT001', 'GT', TRUE, TRUE, 'DELIVERED',
             100.0, 80.0, 70.0, 30.0, 10.0, 8.0, 7.0, 3.0,
             TRUE, FALSE, FALSE, FALSE),
            (5, DATE '2026-03-12', DATE '2026-03-13', 'West', 'West', 'WH02',
             'RT0002', 'OUT002', 'MT', TRUE, TRUE, 'DELIVERED',
             10.0, 10.0, 10.0, 0.0, 1.0, 1.0, 1.0, 0.0,
             TRUE, TRUE, FALSE, TRUE)
        ) t(
            order_id, order_date, requested_delivery_date, customer_region_name,
            warehouse_region_name, warehouse_code, route_code, outlet_code, channel,
            is_eligible_service, is_eligible_service_outlet, order_status,
            ordered_eaches, allocated_eaches, delivered_eaches, short_eaches,
            ordered_case_equivalents, allocated_case_equivalents,
            delivered_case_equivalents, short_case_equivalents,
            on_time_by_timestamp, strict_in_full, late_over_2h, strict_otif
        );

        CREATE TABLE fct_delivery AS
        SELECT * FROM (VALUES
            (1, DATE '2026-04-11', 'West', 'West', 'WH01', 'RT0001', 'OUT001', 'GT',
             TRUE, TRUE, TRUE, 'DN-1', 'Vendor A',
             TIMESTAMP '2026-04-11 10:00:00', TIMESTAMP '2026-04-11 09:30:00',
             -20, -30, TRUE, TRUE, FALSE, TRUE, FALSE, 'DELIVERED', NULL),
            (2, DATE '2026-04-13', 'West', 'West', 'WH02', 'RT0002', 'OUT002', 'MT',
             TRUE, TRUE, FALSE, 'DN-2', 'Vendor B',
             TIMESTAMP '2026-04-13 10:00:00', TIMESTAMP '2026-04-13 13:00:00',
             -5, 180, FALSE, TRUE, TRUE, FALSE, TRUE, 'FAILED', 'DF01')
        ) t(
            delivery_id, delivery_date, customer_region_name, warehouse_region_name,
            warehouse_code, route_code, outlet_code, channel, is_eligible_service,
            has_chilled_product, temperature_excursion_flag, delivery_note_number,
            telematics_vendor, planned_arrival_ts, actual_arrival_ts,
            stored_delay_minutes, derived_delay_minutes, on_time_by_timestamp,
            on_time_by_stored_delay, late_over_2h, pod_captured,
            delay_source_conflict, delivery_status,
            failure_reason_code
        );

        CREATE TABLE fct_order_line AS
        SELECT * FROM (VALUES
            (1, DATE '2026-04-11', 'West', 'West', 'WH01', 'RT0001', 'OUT001', 'GT',
             TRUE, 900.0),
            (2, DATE '2026-04-13', 'West', 'West', 'WH02', 'RT0002', 'OUT002', 'MT',
             TRUE, 100.0)
        ) t(
            order_line_id, requested_delivery_date, customer_region_name,
            warehouse_region_name, warehouse_code, route_code, outlet_code, channel,
            is_eligible_service, estimated_dispatch_value_inr
        );

        CREATE TABLE fct_return_credit_note AS
        SELECT * FROM (VALUES
            (1, DATE '2026-04-20', 'West', 'West', 'WH01', 'RT0001', 'OUT001', 'GT',
             'APPROVED', 50.0, 'CN-1', 101, 1001, 'Outlet One', 'SKU-1',
             'Chilled Product', 'EACH', -2.0, 2.0, TRUE, 2.0, 0.2,
             'RT06_COLD_CHAIN_BREACH', TRUE, 'DESTROY')
        ) t(
            return_id, return_date, customer_region_name, warehouse_region_name,
            warehouse_code, route_code, outlet_code, channel, credit_note_status,
            credit_note_value_inr, credit_note_number, order_id, order_line_id,
            outlet_name, sku_code, product_name, qty_uom, return_qty_raw,
            return_qty_normalized, return_sign_was_negative, return_eaches,
            return_case_equivalents, return_reason_code, is_cold_chain_return,
            disposition
        );

        CREATE TABLE fct_inventory_snapshot AS
        SELECT * FROM (VALUES
            (1, DATE '2026-04-27', 'West', 'WH01', 30, 30.0, 0.0, 0.0),
            (2, DATE '2026-04-27', 'West', 'WH02', 31, 20.0, 0.0, 0.0)
        ) t(
            snapshot_id, snapshot_date, warehouse_region_name, warehouse_code,
            expiry_days, available_cases, damaged_cases, blocked_cases
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
