from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

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
                ('WH01', 'RT0001', DATE '2026-05-15', 100, TRUE),
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
                ('FI-0', 'CR-1', 'Carrier One', 'WH01', 'RT0001',
                 DATE '2026-05-15', 1500, 0, 'PAID'),
                ('FI-1', 'CR-1', 'Carrier One', 'WH01', 'RT0001',
                 DATE '2026-06-14', 1000, 100, 'PAID'),
                ('FI-2', 'CR-1', 'Carrier One', 'WH01', 'RT0001',
                 DATE '2026-06-15', 100, 10, 'PENDING');

            CREATE TABLE dim_product (
                product_id BIGINT,
                sku_code VARCHAR,
                product_name VARCHAR,
                category VARCHAR,
                current_mrp_inr DOUBLE,
                pack_size_value DOUBLE,
                pack_size_uom VARCHAR
            );
            INSERT INTO dim_product VALUES
                (1, 'SKU-1', 'Product One', 'Dairy', 120, 1, 'KG'),
                (2, 'SKU-2', 'Product Two', 'Snacks', 90, 500, 'G');

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
                ('L1', 1, 'SKU-1', 'Product One 1kg', 1.0, 'KG', 100.0, TRUE,
                 'matched', 0.95, 'automatic_match', 'Mumbai', 'Retailer A',
                 DATE '2026-06-30'),
                ('L2', 1, 'SKU-1', 'Product One 1kg', 1.0, 'KG', 105.0, TRUE,
                 'matched', 0.94, 'automatic_match', 'Mumbai', 'Retailer B',
                 DATE '2026-06-29'),
                ('L3', 1, 'SKU-1', 'Product One 500g', 500.0, 'G', 50.0, TRUE,
                 'matched', 0.93, 'automatic_match', 'Mumbai', 'Retailer C',
                 DATE '2026-06-30')
            ) AS observed(
                listing_id, product_id, sku_code, raw_title,
                observed_pack_value, observed_pack_uom,
                current_price_inr, is_available, match_status, match_confidence,
                match_provenance, city, retailer, last_seen
            );
            """
        )


def _filters(**kwargs: object) -> FilterSet:
    return FilterSet(
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        **kwargs,  # type: ignore[arg-type]
    )


def test_external_order_scope_includes_promotion_and_source_filters() -> None:
    where, parameters = ExternalAnalyticsService._order_filter_sql(
        _filters(promotion_codes=("PROMO-1",), order_sources=("ERP",))
    )

    assert "promotion_code IN (?)" in where
    assert "source_system IN (?)" in where
    assert parameters[-2:] == ["PROMO-1", "ERP"]


def test_freight_cost_per_case_uses_independent_warehouse_aggregates(
    tmp_path: Path,
) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).freight_by_warehouse(_filters())

    assert len(frame) == 1
    assert frame.iloc[0]["freight_cost_inr"] == Decimal("1100.00")
    assert frame.iloc[0]["delivered_case_equivalents"] == 100
    assert frame.iloc[0]["settled_freight_cost_per_delivered_case_inr"] == 10
    assert frame.iloc[0]["freight_cost_per_delivered_case_inr"] == 11
    assert "independently aggregated" in frame.attrs["attribution"]


def test_freight_reports_filters_that_cannot_be_attributed(tmp_path: Path) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).freight_by_warehouse(
        _filters(
            customer_regions=("West",),
            outlet_codes=("OUT1",),
            promotion_codes=("PROMO-1",),
            order_sources=("ERP",),
        )
    )

    assert frame.attrs["ignored_filters"] == (
        "customer region",
        "outlet",
        "promotion",
        "order source",
    )


def test_freight_geography_lenses_never_apply_a_composite_pair(
    tmp_path: Path,
) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)
    filters = _filters(warehouse_codes=("WH01",), route_codes=("RT0001",))
    service = ExternalAnalyticsService(database)

    warehouse = service.freight_by_warehouse(filters)
    route = service.freight_by_route(filters)

    assert "route (independent route lens)" in warehouse.attrs["ignored_filters"]
    assert "warehouse (independent warehouse lens)" in route.attrs["ignored_filters"]
    warehouse_where, _ = service._shared_freight_filters(
        filters,
        date_column="invoice.service_date",
        warehouse_alias="warehouse",
        route_column="invoice.route_code",
        include_route=False,
    )
    route_where, _ = service._shared_freight_filters(
        filters,
        date_column="invoice.service_date",
        warehouse_alias="warehouse",
        route_column="invoice.route_code",
        include_warehouse=False,
    )
    assert "invoice.route_code" not in warehouse_where
    assert "warehouse.warehouse_code" not in route_where


def test_freight_cost_per_case_supports_independent_route_grain(tmp_path: Path) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).freight_by_route(_filters())

    assert len(frame) == 1
    assert frame.iloc[0]["route_code"] == "RT0001"
    assert frame.iloc[0]["route_name"] == "Mumbai Route 1"
    assert frame.iloc[0]["freight_cost_inr"] == Decimal("1100.00")
    assert frame.iloc[0]["delivered_case_equivalents"] == 100
    assert frame.iloc[0]["settled_freight_cost_per_delivered_case_inr"] == 10
    assert frame.iloc[0]["freight_cost_per_delivered_case_inr"] == 11
    assert "service period × route" in frame.attrs["attribution"]


def test_route_performance_uses_prior_equivalent_period_and_evidence_floors(
    tmp_path: Path,
) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).freight_route_performance(
        _filters(),
        min_invoices=1,
        min_deliveries=1,
        ranking="most_improved",
    )

    assert list(frame["route_code"]) == ["RT0001"]
    row = frame.iloc[0]
    assert row["current_invoice_count"] == 2
    assert row["prior_invoice_count"] == 1
    assert row["current_freight_cost_per_delivered_case_inr"] == 11
    assert row["prior_freight_cost_per_delivered_case_inr"] == 15
    assert row["cost_per_case_delta_inr"] == -4
    assert row["cost_per_case_delta_pct"] == pytest.approx(-26.6666667)
    assert frame.attrs["prior_period"] == (date(2026, 5, 2), date(2026, 5, 31))


def test_route_performance_rejects_invalid_ranking_and_floors(tmp_path: Path) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)
    service = ExternalAnalyticsService(database)

    with pytest.raises(ValueError, match="positive"):
        service.freight_route_performance(_filters(), min_invoices=0)
    with pytest.raises(ValueError, match="ranking"):
        service.freight_route_performance(
            _filters(),
            ranking="fastest",  # type: ignore[arg-type]
        )


def test_competitor_gap_keeps_unmatched_top_skus_visible(tmp_path: Path) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).competitor_price_gap(
        _filters(), city="Mumbai", top_n=2
    )

    assert list(frame["sku_code"]) == ["SKU-1", "SKU-2"]
    assert frame.iloc[0]["lowest_competitor_price_inr"] == 100
    assert frame.iloc[0]["price_gap_inr"] == 20
    assert frame.iloc[0]["competitor_listing_id"] == "L1"
    assert frame.iloc[0]["competitor_retailer"] == "Retailer A"
    assert frame.iloc[0]["pack_comparable"]
    assert frame.iloc[0]["unit_price_basis"] == "100 G"
    assert frame.iloc[0]["lowest_competitor_unit_price_inr"] == 10
    assert frame.iloc[0]["kestrel_mrp_unit_inr"] == 12
    assert frame.iloc[0]["matched_listings"] == 2
    assert frame.iloc[0]["retailers"] == 2
    assert frame.attrs["matched_top_skus"] == 1
    assert frame.iloc[1]["lowest_competitor_price_inr"] != frame.iloc[1][
        "lowest_competitor_price_inr"
    ]


def test_current_comparable_matches_returns_every_qualifying_listing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)
    service = ExternalAnalyticsService(database)

    frame = service.competitor_current_comparable_matches(city="Mumbai")
    retailer = service.competitor_current_comparable_matches(
        city="Mumbai", retailer="Retailer B"
    )

    assert frame["listing_id"].tolist() == ["L1", "L2"]
    assert frame["competitor_unit_price_inr"].tolist() == [10, 10.5]
    assert frame["unit_price_basis"].tolist() == ["100 G", "100 G"]
    assert retailer["listing_id"].tolist() == ["L2"]

    catalog = service.competitor_matched_listing_catalog(city="Mumbai")
    assert catalog["listing_id"].tolist() == ["L1", "L2", "L3"]


def test_competitor_gap_filters_retailer_and_as_of_without_losing_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "external.duckdb"
    _external_database(database)

    frame = ExternalAnalyticsService(database).competitor_price_gap(
        _filters(),
        city="Mumbai",
        retailer="Retailer B",
        as_of=date(2026, 6, 29),
        top_n=1,
    )

    assert frame.iloc[0]["lowest_competitor_price_inr"] == 105
    assert frame.iloc[0]["competitor_listing_id"] == "L2"
    assert frame.iloc[0]["competitor_retailer"] == "Retailer B"
    assert frame.iloc[0]["matched_listings"] == 1
    assert frame.attrs["retailer"] == "Retailer B"
    assert frame.attrs["as_of"] == date(2026, 6, 29)
