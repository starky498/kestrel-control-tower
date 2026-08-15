from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from kestrel.ingestion.bazaarpulse import (
    Listing,
    ProductMatch,
    SourceDetailFailure,
    SourcePriceObservation,
)
from kestrel.ingestion.freight import FreightInvoice, FreightSyncMetadata
from kestrel.integration_store import (
    load_product_candidates,
    store_bazaarpulse_snapshot,
    store_freight_snapshot,
)
from kestrel.metrics.external import ExternalAnalyticsService
from kestrel.metrics.service import FilterSet


def _database(path: Path) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE dim_product (
                product_id BIGINT, sku_code VARCHAR, product_name VARCHAR,
                brand VARCHAR, category VARCHAR, pack_size_value DOUBLE,
                pack_size_uom VARCHAR, current_mrp_inr DOUBLE
            );
            INSERT INTO dim_product VALUES
                (1, 'SKU-1', 'Kestrel Milk', 'Kestrel', 'Dairy', 1, 'L', 75);

            CREATE TABLE raw_product_price_history (
                price_history_id BIGINT, product_id BIGINT, effective_from DATE,
                effective_to DATE, mrp_inr DOUBLE, list_price_inr DOUBLE
            );
            INSERT INTO raw_product_price_history VALUES
                (1, 1, DATE '2026-01-01', DATE '2026-06-25', 70, 68),
                (2, 1, DATE '2026-06-26', NULL, 75, 72)
            """
        )


def _listing() -> Listing:
    return Listing(
        listing_id="101",
        city="Mumbai",
        retailer="ValueMart",
        raw_title="Kestrel Milk 1L",
        normalized_title="kestrel milk",
        brand="Kestrel",
        pack_value=1,
        pack_uom="L",
        category="Dairy",
        current_price_inr=69,
        mrp_inr=75,
        is_available=True,
        last_seen=date(2026, 6, 30),
        detail_path="/product/101.html",
        source_path="/city/mumbai/page/1.html",
    )


def _source_price(observed_on: date, price: float) -> SourcePriceObservation:
    return SourcePriceObservation(
        listing_id="101",
        city="Mumbai",
        retailer="ValueMart",
        raw_title="Kestrel Milk 1L",
        category="Dairy",
        pack_value=1,
        pack_uom="L",
        observed_on=observed_on,
        price_inr=price,
        source_path="/product/101.html",
    )


def test_bazaarpulse_snapshot_is_queryable_and_auditable(tmp_path: Path) -> None:
    database = tmp_path / "analytics.duckdb"
    _database(database)
    match = ProductMatch(
        listing_id="101",
        status="matched",
        confidence=0.98,
        runner_up_confidence=0.3,
        product_id=1,
        sku_code="SKU-1",
        reason="Strong title and pack agreement.",
    )
    completed = datetime(2026, 7, 1, 8, tzinfo=UTC)

    summary = store_bazaarpulse_snapshot(
        database, [_listing()], [match], completed_at_utc=completed
    )

    assert summary.record_count == 1
    assert summary.matched_count == 1
    candidates = load_product_candidates(database)
    assert candidates[0].sku_code == "SKU-1"
    with duckdb.connect(str(database), read_only=True) as connection:
        row = connection.execute(
            """
            SELECT city, current_price_inr, kestrel_mrp_inr, match_status
            FROM vw_competitor_price_current
            """
        ).fetchone()
        sync = connection.execute(
            "SELECT source_name, record_count, is_complete FROM external_sync_runs"
        ).fetchone()
    assert row == ("Mumbai", 69.0, 75.0, "matched")
    assert sync == ("bazaarpulse", 1, True)


def test_empty_snapshot_cannot_destroy_last_good_data(tmp_path: Path) -> None:
    database = tmp_path / "analytics.duckdb"
    _database(database)

    with pytest.raises(ValueError, match="last-good"):
        store_bazaarpulse_snapshot(database, [], [])


def test_detail_failures_are_persisted_and_mark_source_history_incomplete(
    tmp_path: Path,
) -> None:
    database = tmp_path / "analytics.duckdb"
    _database(database)
    match = ProductMatch(
        listing_id="101",
        status="matched",
        confidence=0.98,
        runner_up_confidence=0.3,
        product_id=1,
        sku_code="SKU-1",
        reason="Strong title and pack agreement.",
    )
    failure = SourceDetailFailure(
        listing_id="101",
        source_path="/product/101.html",
        failure_type="missing_detail_page",
        message="Linked product detail page is absent from the supplied site.",
    )

    store_bazaarpulse_snapshot(
        database,
        [_listing()],
        [match],
        source_detail_failures=[failure],
        completed_at_utc=datetime(2026, 7, 1, 8, tzinfo=UTC),
    )

    with duckdb.connect(str(database), read_only=True) as connection:
        sync = connection.execute(
            """
            SELECT status, is_complete,
                   cast(json_extract(details_json, '$.source_price_history_complete') AS BOOLEAN)
            FROM external_sync_runs
            """
        ).fetchone()
    failures = ExternalAnalyticsService(database).competitor_source_detail_failures()
    assert sync == ("SUCCEEDED_WITH_WARNINGS", False, False)
    assert failures.iloc[0]["listing_id"] == "101"
    assert failures.iloc[0]["failure_type"] == "missing_detail_page"


def test_source_price_history_is_distinct_idempotent_and_uses_effective_mrp(
    tmp_path: Path,
) -> None:
    database = tmp_path / "analytics.duckdb"
    _database(database)
    match = ProductMatch(
        listing_id="101",
        status="matched",
        confidence=0.98,
        runner_up_confidence=0.3,
        product_id=1,
        sku_code="SKU-1",
        reason="Strong title and pack agreement.",
    )
    completed = datetime(2026, 7, 1, 8, tzinfo=UTC)
    source_prices = [
        _source_price(date(2025, 12, 20), 55),
        _source_price(date(2026, 6, 20), 60),
        _source_price(date(2026, 6, 27), 65),
    ]

    store_bazaarpulse_snapshot(
        database,
        [_listing()],
        [match],
        source_price_observations=source_prices,
        completed_at_utc=completed,
    )
    store_bazaarpulse_snapshot(
        database,
        [_listing()],
        [match],
        source_price_observations=source_prices,
        completed_at_utc=completed,
    )

    with duckdb.connect(str(database), read_only=True) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM ext_bazaarpulse_listing_history),
                (SELECT count(*) FROM ext_bazaarpulse_source_price_observation)
            """
        ).fetchone()
        history = connection.execute(
            """
            SELECT observed_on, observed_price_inr, historical_kestrel_mrp_inr,
                   pack_comparable, unit_price_basis,
                   historical_mrp_premium_pct, mrp_history_available
            FROM vw_competitor_source_price_history
            ORDER BY observed_on
            """
        ).fetchall()
    assert counts == (1, 3)
    assert history[0][:5] == (date(2025, 12, 20), 55.0, None, True, "100 ML")
    assert history[0][5] is None
    assert history[0][6] is False
    assert history[1][:5] == (date(2026, 6, 20), 60.0, 70.0, True, "100 ML")
    assert history[1][5] == pytest.approx((70 / 60 - 1) * 100)
    assert history[1][6] is True
    assert history[2][:5] == (date(2026, 6, 27), 65.0, 75.0, True, "100 ML")

    service = ExternalAnalyticsService(database)
    filtered = service.competitor_source_price_history(
        city="Mumbai",
        retailer="ValueMart",
        observed_from=date(2026, 1, 1),
        as_of=date(2026, 6, 25),
        matched_only=True,
    )
    coverage = service.competitor_source_price_coverage(city="Mumbai")
    assert filtered.iloc[0]["observed_on"].date() == date(2026, 6, 20)
    assert filtered.iloc[0]["historical_kestrel_mrp_inr"] == 70
    assert "current MRP is never substituted" in filtered.attrs["mrp_methodology"]
    assert coverage.iloc[0]["source_price_observations"] == 3

    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            CREATE TABLE fct_order_line (
                order_id BIGINT, sku_code VARCHAR, product_name VARCHAR,
                category VARCHAR, ordered_case_equivalents DOUBLE,
                delivered_case_equivalents DOUBLE, short_case_equivalents DOUBLE,
                short_delivery_value_exposure_inr DOUBLE,
                estimated_dispatch_value_inr DOUBLE, requested_delivery_date DATE,
                customer_region_name VARCHAR, warehouse_region_name VARCHAR,
                warehouse_code VARCHAR, route_code VARCHAR, outlet_code VARCHAR,
                channel VARCHAR, is_eligible_service BOOLEAN
            );
            INSERT INTO fct_order_line VALUES
                (1, 'SKU-1', 'Kestrel Milk', 'Dairy', 100, 80, 20, 1500, 6000,
                 DATE '2026-06-10', 'West', 'West', 'WH01', 'RT01', 'OUT1', 'GT', TRUE)
            """
        )
    attention = service.competitor_service_price_attention(
        FilterSet(date(2026, 6, 1), date(2026, 6, 30)),
        city="Mumbai",
        retailer="ValueMart",
        as_of=date(2026, 6, 25),
    )
    assert attention.iloc[0]["sku_code"] == "SKU-1"
    assert attention.iloc[0]["line_fill_rate_pct"] == 80
    assert attention.iloc[0]["observed_on"].date() == date(2026, 6, 20)
    assert attention.iloc[0]["historical_kestrel_mrp_inr"] == 70
    assert attention.iloc[0]["price_evidence_status"] == "COMPARABLE"
    assert "not evidence" in attention.attrs["methodology"]


def test_changed_source_price_for_same_listing_date_is_rejected_atomically(
    tmp_path: Path,
) -> None:
    database = tmp_path / "analytics.duckdb"
    _database(database)
    match = ProductMatch(
        listing_id="101",
        status="matched",
        confidence=0.98,
        runner_up_confidence=0.3,
        product_id=1,
        sku_code="SKU-1",
        reason="Strong title and pack agreement.",
    )
    store_bazaarpulse_snapshot(
        database,
        [_listing()],
        [match],
        source_price_observations=[_source_price(date(2026, 6, 20), 60)],
        completed_at_utc=datetime(2026, 7, 1, 8, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="source price observation identity"):
        store_bazaarpulse_snapshot(
            database,
            [_listing()],
            [match],
            source_price_observations=[_source_price(date(2026, 6, 20), 61)],
            completed_at_utc=datetime(2026, 7, 2, 8, tzinfo=UTC),
        )

    with duckdb.connect(str(database), read_only=True) as connection:
        state = connection.execute(
            """
            SELECT
                (SELECT price_inr FROM ext_bazaarpulse_source_price_observation),
                (SELECT count(*) FROM external_sync_runs WHERE source_name = 'bazaarpulse')
            """
        ).fetchone()
    assert state == (60.0, 1)


def test_complete_freight_snapshot_preserves_source_and_converted_units(
    tmp_path: Path,
) -> None:
    database = tmp_path / "analytics.duckdb"
    _database(database)
    invoice = FreightInvoice.model_validate(
        {
            "invoice_id": "FI-1",
            "carrier_id": "CR-101",
            "carrier_name": "Bluewheel",
            "warehouse_code": "WH01",
            "route_code": "RT0001",
            "invoice_date": "2026-06-15",
            "service_date": "2026-06-14",
            "amount": 12_345,
            "currency": "INR",
            "fuel_surcharge_pct": 8.25,
            "detention_charge": 250,
            "distance_km": 42.5,
            "weight_kg": 812.4,
            "temperature_controlled": True,
            "status": "PAID",
            "created_at_utc": "2026-06-15T20:15:00Z",
        }
    )
    started = datetime(2026, 7, 1, 7, tzinfo=UTC)
    completed = datetime(2026, 7, 1, 8, tzinfo=UTC)
    metadata = FreightSyncMetadata(
        complete=True,
        cache_mode="range_upsert",
        source_url="https://freight.test/v1/freight_invoices",
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        started_at_utc=started,
        completed_at_utc=completed,
        page_count=1,
        request_count=1,
        retry_count=0,
        fetched_count=1,
        record_count=1,
    )

    summary = store_freight_snapshot(database, (invoice,), metadata)
    store_freight_snapshot(database, (invoice,), metadata)

    assert summary.coverage_start == date(2026, 6, 14)
    with duckdb.connect(str(database), read_only=True) as connection:
        row = connection.execute(
            """
            SELECT amount_paise, amount_inr, detention_charge_inr,
                   billed_freight_cost_inr, created_at_ist
            FROM ext_freight_invoice_current
            """
        ).fetchone()
        sync_rows = connection.execute(
            "SELECT count(*) FROM external_sync_runs WHERE source_name = 'freight_api'"
        ).fetchone()[0]
    assert row[:4] == (
        12_345,
        Decimal("123.45"),
        Decimal("2.50"),
        Decimal("125.95"),
    )
    assert row[4].astimezone(ZoneInfo("Asia/Kolkata")).isoformat() == (
        "2026-06-16T01:45:00+05:30"
    )
    assert sync_rows == 1


def test_incomplete_freight_snapshot_is_never_published(tmp_path: Path) -> None:
    database = tmp_path / "analytics.duckdb"
    _database(database)
    metadata = FreightSyncMetadata(
        complete=False,
        cache_mode="range_upsert",
        source_url="https://freight.test/v1/freight_invoices",
        started_at_utc=datetime(2026, 7, 1, 7, tzinfo=UTC),
        page_count=0,
        request_count=1,
        retry_count=0,
        fetched_count=0,
        record_count=0,
        error="timeout",
    )

    with pytest.raises(ValueError, match="incomplete"):
        store_freight_snapshot(database, (), metadata)
