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
             'RT0001', 'OUT001', 'GT', TRUE, 100.0, 90.0, 10.0, 10.0, 9.0, 1.0,
             TRUE, FALSE, FALSE, FALSE),
            (2, DATE '2026-04-12', DATE '2026-04-13', 'West', 'West', 'WH02',
             'RT0002', 'OUT002', 'MT', TRUE, 10.0, 10.0, 0.0, 1.0, 1.0, 0.0,
             FALSE, TRUE, TRUE, FALSE)
        ) t(
            order_id, order_date, requested_delivery_date, customer_region_name,
            warehouse_region_name, warehouse_code, route_code, outlet_code, channel,
            is_eligible_service, ordered_eaches, delivered_eaches, short_eaches,
            ordered_case_equivalents, delivered_case_equivalents, short_case_equivalents,
            on_time_by_timestamp, strict_in_full, late_over_2h, strict_otif
        );

        CREATE TABLE fct_delivery AS
        SELECT * FROM (VALUES
            (1, DATE '2026-04-11', 'West', 'West', 'WH01', 'RT0001', 'OUT001', 'GT',
             TRUE, TRUE, TRUE),
            (2, DATE '2026-04-13', 'West', 'West', 'WH02', 'RT0002', 'OUT002', 'MT',
             TRUE, TRUE, FALSE)
        ) t(
            delivery_id, delivery_date, customer_region_name, warehouse_region_name,
            warehouse_code, route_code, outlet_code, channel, is_eligible_service,
            has_chilled_product, temperature_excursion_flag
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
             'APPROVED', 50.0)
        ) t(
            return_id, return_date, customer_region_name, warehouse_region_name,
            warehouse_code, route_code, outlet_code, channel, credit_note_status,
            credit_note_value_inr
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
    assert summary["near_expiry_cases"].value == 30


def test_service_dimension_is_allow_listed(service: AnalyticsService) -> None:
    filters = FilterSet(date(2026, 4, 1), date(2026, 4, 30))
    with pytest.raises(ValueError, match="Unsupported service dimension"):
        service.service_by_dimension(filters, "arbitrary_sql")
