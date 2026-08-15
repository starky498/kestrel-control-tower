from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from kestrel.ingestion.bazaarpulse import Listing, ProductMatch
from kestrel.ingestion.freight import FreightInvoice, FreightSyncMetadata
from kestrel.integration_store import (
    load_product_candidates,
    store_bazaarpulse_snapshot,
    store_freight_snapshot,
)


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
                (1, 'SKU-1', 'Kestrel Milk', 'Kestrel', 'Dairy', 1, 'L', 75)
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
