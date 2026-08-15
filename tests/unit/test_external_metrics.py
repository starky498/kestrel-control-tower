from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from kestrel.metrics.external import ExternalAnalyticsService
from kestrel.metrics.service import FilterSet


def _external_database(path: Path) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE dim_warehouse (
                warehouse_code VARCHAR,
                warehouse_name VARCHAR,
                warehouse_region_name VARCHAR
            );
            INSERT INTO dim_warehouse VALUES ('WH01', 'Mumbai DC', 'West');

            CREATE TABLE dim_route (
                route_code VARCHAR,
                route_name VARCHAR
            );
            INSERT INTO dim_route VALUES ('RT0001', 'Mumbai Route 1');

            CREATE TABLE fct_order_service (
                warehouse_code VARCHAR,
                route_code VARCHAR,
                delivery_date DATE,
                delivered_case_equivalents DOUBLE,
                is_eligible_service BOOLEAN
            );
            INSERT INTO fct_order_service VALUES
                ('WH01', 'RT0001', DATE '2026-06-14', 60, TRUE),
                ('WH01', 'RT0001', DATE '2026-06-15', 40, TRUE);

            CREATE TABLE ext_freight_invoice_current (
                invoice_id VARCHAR,
                carrier_id VARCHAR,
                carrier_name VARCHAR,
                warehouse_code VARCHAR,
                route_code VARCHAR,
                service_date DATE,
                billed_freight_cost_inr DECIMAL(18, 2),
                detention_charge_inr DECIMAL(18, 2),
                invoice_status VARCHAR
            );
            INSERT INTO ext_freight_invoice_current VALUES
                ('FI-1', 'CR-1', 'Carrier One', 'WH01', 'RT0001',
                 DATE '2026-06-14', 1000, 100, 'PAID'),
                ('FI-2', 'CR-1', 'Carrier One', 'WH01', 'RT0001',
                 DATE '2026-06-15', 100, 10, 'PENDING');

            CREATE TABLE dim_product (
                product_id BIGINT,
                sku_code VARCHAR,
                current_mrp_inr DOUBLE
            );
            INSERT INTO dim_product VALUES (1, 'SKU-1', 120), (2, 'SKU-2', 90);

            CREATE TABLE fct_order_line (
                product_id BIGINT,
                sku_code VARCHAR,
                product_name VARCHAR,
                category VARCHAR,
                estimated_dispatch_value_inr DOUBLE,
                requested_delivery_date DATE,
                customer_region_name VARCHAR,
                warehouse_region_name VARCHAR,
                warehouse_code VARCHAR,
                route_code VARCHAR,
                outlet_code VARCHAR,
                channel VARCHAR,
                is_eligible_service BOOLEAN
            );
            INSERT INTO fct_order_line VALUES
                (1, 'SKU-1', 'Product One', 'Dairy', 5000, DATE '2026-06-10',
                 'West', 'West', 'WH01', 'RT0001', 'OUT1', 'GT', TRUE),
                (2, 'SKU-2', 'Product Two', 'Snacks', 3000, DATE '2026-06-10',
                 'West', 'West', 'WH01', 'RT0001', 'OUT1', 'GT', TRUE);

            CREATE TABLE ext_bazaarpulse_listing_current (listing_id VARCHAR);
            CREATE VIEW vw_competitor_price_current AS
            SELECT * FROM (VALUES
                ('SKU-1', 100.0, TRUE, 'matched', 'Mumbai', 'Retailer A', DATE '2026-06-30'),
                ('SKU-1', 105.0, TRUE, 'matched', 'Mumbai', 'Retailer B', DATE '2026-06-29')
            ) AS observed(
                sku_code, current_price_inr, is_available, match_status,
                city, retailer, last_seen
            );
            """
        )


def _filters(**kwargs: object) -> FilterSet:
    return FilterSet(
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        **kwargs,  # type: ignore[arg-type]
    )


def test_freight_cost_per_case_uses_independent_warehouse_aggregates(
    tmp_path: Path,
) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).freight_by_warehouse(_filters())

    assert len(frame) == 1
    assert frame.iloc[0]["freight_cost_inr"] == Decimal("1100.00")
    assert frame.iloc[0]["delivered_case_equivalents"] == 100
    assert frame.iloc[0]["freight_cost_per_delivered_case_inr"] == 11
    assert "independently aggregated" in frame.attrs["attribution"]


def test_freight_reports_filters_that_cannot_be_attributed(tmp_path: Path) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).freight_by_warehouse(
        _filters(customer_regions=("West",), outlet_codes=("OUT1",))
    )

    assert frame.attrs["ignored_filters"] == ("customer region", "outlet")


def test_freight_cost_per_case_supports_independent_route_grain(tmp_path: Path) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).freight_by_route(_filters())

    assert len(frame) == 1
    assert frame.iloc[0]["route_code"] == "RT0001"
    assert frame.iloc[0]["route_name"] == "Mumbai Route 1"
    assert frame.iloc[0]["freight_cost_inr"] == Decimal("1100.00")
    assert frame.iloc[0]["delivered_case_equivalents"] == 100
    assert frame.iloc[0]["freight_cost_per_delivered_case_inr"] == 11
    assert "service period × route" in frame.attrs["attribution"]


def test_competitor_gap_keeps_unmatched_top_skus_visible(tmp_path: Path) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).competitor_price_gap(
        _filters(), city="Mumbai", top_n=2
    )

    assert list(frame["sku_code"]) == ["SKU-1", "SKU-2"]
    assert frame.iloc[0]["lowest_competitor_price_inr"] == 100
    assert frame.iloc[0]["price_gap_inr"] == 20
    assert frame.attrs["matched_top_skus"] == 1
    assert frame.iloc[1]["lowest_competitor_price_inr"] != frame.iloc[1][
        "lowest_competitor_price_inr"
    ]
